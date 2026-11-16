"""Spot Bitcoin ETF flows: the funds' wallets watched on-chain, plus the published daily numbers.

Two different things are called "ETF flow", and this keeps them apart:

- **On-chain** (live). Bitcoin moving in or out of a fund's wallets. Seen within about a minute,
  from the mempool on. Only possible for funds whose wallet addresses are known:
    * BITB publishes its own list (inside bitbetf.com's page data); it is re-read every 15 minutes.
    * Every other issuer keeps its addresses private (Coinbase, custodian for most of them, does
      not disclose client wallets). Third parties (Arkham, Dune) attribute addresses, behind API
      keys. Put any list you obtain in <data-dir>/etf_wallets.json and it is tracked the same way:
          {"IBIT": ["bc1q...", "3..."], "FBTC": {"addresses": ["..."], "source": "Arkham export"}}
      A fund's entry may also carry "redeems_to": [address] (an address only that fund's redemptions
      are paid into: whatever one wallet pays it is an outflow, and that wallet joins the fund) and
      "adopts": "p2wsh" (the fund's own spends' fresh P2WSH outputs are its new cold storage).
      Long lists are balance-checked in turns, and lists over BIG_LIST addresses are not back-filled.
- **Published** (daily). What the funds report after the US close, for every fund:
    * bitbo.io/treasuries/etf-flows: bitcoin added or removed per fund per day (holdings changes).
    * CoinMarketCap's ETF pages: total net flow per day in USD, and each fund's assets.

A flow is one transaction's net effect on one fund: bitcoin received by the fund's addresses minus
bitcoin spent from them. Moves between two wallets of the same fund net to zero and are dropped.
When a fund's list grows, the transactions of the new addresses are re-read, so a move that first
looked like an outflow (to a wallet not yet on the list) is corrected to an internal move.

No keys. Balances come from blockchain.info (100 addresses per request), transactions from
mempool.space (blockstream.info as the fallback).

What snapshot() returns (GET /api/etfs; the websocket's {"type": "etfs"} carries the same without
`onchain_daily` and `published`, and with the 60 newest events instead of 500):

    ready, updated (ms), price (BTC now)
    funds[]   ticker, name, issuer, custodian
              wallets: "issuer" | "file" | "private"; wallet_source; list_published (ms)
              addresses, addresses_funded, syncing (addresses still being read)
              tracked_btc; holdings_btc (+ holdings_estimated); coverage_pct
              aum, share_price, volume, premium_pct
              onchain: {today, h24, d7, d30} each {in, out, net, net_usd, n} in BTC, or null if not watched
              published_last: {date, btc} the latest non-zero day; published_5d (BTC)
    totals    tracked[], untracked[], tracked_btc, aum, onchain {today, h24, d7, d30},
              published_usd_last {date, usd}, published_usd_5d, published_btc_date
    events[]  newest first: ticker, txid, time (ms; block time once confirmed), seen (ms), btc (+ in, - out),
              px, usd, confirmed, cp (largest counterparty address), cp_n, live (seen as it happened,
              not read from history), unsettled (an outflow newer than the issuer's last list)
    sources   {chain, bitwise, wallet_file, bitbo, coinmarketcap}: {ok, at (ms of last success), error}
    onchain_daily   {ticker: [[day_ms, in_btc, out_btc], ...]}, UTC days, last 30
    published       tickers[]; by_fund [[date, {ticker: btc}], ...]; total_usd [[day_ms, usd], ...];
                    total_btc and issuer_btc {issuer: [[day_ms, btc], ...]} back to January 2024
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

log = logging.getLogger("btcliq")

CHAIN_SECONDS = 60          # balances of every tracked address
WALLET_SECONDS = 900        # issuer-published lists and your etf_wallets.json
PUBLISHED_SECONDS = 1800    # the daily numbers
BALANCE_API = "https://blockchain.info/balance"
ESPLORA = ("https://mempool.space/api", "https://blockstream.info/api")
BITWISE_URL = "https://bitbetf.com/"
BITBO_URL = "https://bitbo.io/treasuries/etf-flows/"
CMC_API = "https://api.coinmarketcap.com/data-api/v3/etf"

SATS = 100_000_000
DUST_SATS = 1_000_000       # ignore net moves under 0.01 BTC: fee top-ups, dust, change rounding
BATCH = 100                 # addresses per balance request
TX_FETCH_PER_CYCLE = 40     # addresses whose transactions are read per cycle (a first sync spreads out)
TX_PAGES = 3                # 25 confirmed transactions per page, for busy addresses
BALANCE_BATCHES = 12        # balance requests per cycle: a longer list is swept in turns (1,200 addresses a minute)
BIG_LIST = 500              # a file list longer than this starts from now, with no 30-day back-fill per address
GATEWAY_PAGES = 6           # first read of a fund's redemption address (25 transactions a page), then the newest page
HISTORY_DAYS = 30           # how far back a first sync reads; older rotations can't be told from flows
KEEP_DAYS = 120             # events kept on disk
DAY_MS = 86_400_000
ADDRESS = re.compile(r"^(bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$")

# The US spot funds. `wallets` says where a fund's addresses come from: "issuer" = the fund publishes
# them; "private" = not disclosed, tracked only if you supply a list.
FUNDS = [
    {"ticker": "IBIT", "name": "iShares Bitcoin Trust", "issuer": "BlackRock", "custodian": "Coinbase Custody", "wallets": "private"},
    {"ticker": "FBTC", "name": "Fidelity Wise Origin Bitcoin Fund", "issuer": "Fidelity", "custodian": "Fidelity Digital Assets", "wallets": "private"},
    {"ticker": "GBTC", "name": "Grayscale Bitcoin Trust", "issuer": "Grayscale", "custodian": "Coinbase Custody", "wallets": "private"},
    {"ticker": "BTC", "name": "Grayscale Bitcoin Mini Trust", "issuer": "Grayscale", "custodian": "Coinbase Custody", "wallets": "private"},
    {"ticker": "BITB", "name": "Bitwise Bitcoin ETF", "issuer": "Bitwise", "custodian": "Coinbase Custody", "wallets": "issuer"},
    {"ticker": "ARKB", "name": "ARK 21Shares Bitcoin ETF", "issuer": "ARK / 21Shares", "custodian": "Coinbase Custody", "wallets": "private"},
    {"ticker": "HODL", "name": "VanEck Bitcoin ETF", "issuer": "VanEck", "custodian": "Gemini", "wallets": "private"},
    {"ticker": "BRRR", "name": "CoinShares Valkyrie Bitcoin Fund", "issuer": "CoinShares", "custodian": "Coinbase Custody", "wallets": "private"},
    {"ticker": "EZBC", "name": "Franklin Bitcoin ETF", "issuer": "Franklin Templeton", "custodian": "Coinbase Custody", "wallets": "private"},
    {"ticker": "BTCO", "name": "Invesco Galaxy Bitcoin ETF", "issuer": "Invesco / Galaxy", "custodian": "Coinbase Custody", "wallets": "private"},
    {"ticker": "BTCW", "name": "WisdomTree Bitcoin Fund", "issuer": "WisdomTree", "custodian": "Coinbase Custody", "wallets": "private"},
]
BITBO_ISSUERS = {"aum_etf_blackrock": "BlackRock", "aum_etf_grayscale": "Grayscale", "aum_etf_fidelity": "Fidelity",
                 "aum_etf_ark": "ARK / 21Shares", "aum_etf_bitwise": "Bitwise", "aum_etf_others": "Others"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _day_ms(iso: str) -> int:
    return int(datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _last_us_close(now_ms: int) -> int:
    """The last US market close (about 21:00 UTC on a weekday): the moment a fund's reported assets refer to."""
    d = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    c = d.replace(hour=21, minute=0, second=0, microsecond=0)
    if c > d:
        c -= timedelta(days=1)
    while c.weekday() >= 5:
        c -= timedelta(days=1)
    return int(c.timestamp() * 1000)


# ---------- parsers (pure, so the tests can feed them saved pages) ----------

def parse_bitwise(html: str) -> dict:
    """BITB's wallet list and holdings from the fund page's embedded data."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Bitwise page has no embedded fund data")
    data = json.loads(m.group(1))["props"]["pageProps"]["fundData"]["data"]
    wallets = data["wallets"]
    rows = [(w["address"], float(w.get("balance") or 0), bool(w.get("active", True)))
            for w in wallets["walletBalances"] if ADDRESS.match(w.get("address") or "")]
    if not rows:
        raise ValueError("Bitwise page lists no wallet addresses")
    basket = (data.get("holdings") or {}).get("basket") or []
    btc = next((float(b["shares"]) for b in basket if "BITCOIN" in (b.get("companyName") or "").upper()), None)
    updated = wallets.get("updatedAt")
    return {
        "wallets": rows,
        "updated": int(datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp() * 1000) if updated else None,
        "holdings_btc": btc,
        "holdings_asof": (data.get("holdings") or {}).get("asOfDate"),
        "shares": (data.get("fundDetails") or {}).get("sharesOutstanding"),
    }


def _bitbo_date(ms: int) -> str:
    """The date bitbo shows for a data point: its page moves each point to the previous business
    day, in US time (a point stamped 00:00 UTC is the evening before there)."""
    d = datetime.fromtimestamp(ms / 1000, timezone.utc).date() - timedelta(days=1)
    d -= timedelta(days={6: 2, 0: 3}.get(d.weekday(), 1))     # Sunday -> Friday, Monday -> Friday, else the day before
    return d.isoformat()


def parse_bitbo(html: str) -> dict:
    """bitbo's flows page: the per-fund table of recent days and the daily series behind its charts.

    Its numbers are bitcoin (holdings changes). The table shows them as US$m at one fixed price, so
    they are turned back into BTC with that price, which the page carries in its chart data. The
    newest row is often incomplete: funds that haven't reported yet show as zero.
    """
    series: dict[str, list] = {}
    price = None
    for name in BITBO_ISSUERS:
        found = re.search(r"\b" + name + r"\s*=\s*\[(.*?)\]\s*;", html, re.S)
        if not found:
            continue
        body = found.group(1)
        days: dict[str, float] = {}
        for ms, btc, mult in re.findall(
                r"getPreviousBusinessDay\((\d+)\)\s*,\s*truncate\(\s*(-?[\d.]+)(?:\s*\*\s*([\d.]+))?\s*,", body):
            day = _bitbo_date(int(ms))
            days[day] = days.get(day, 0.0) + float(btc)
            price = price or (float(mult) if mult else None)
        series[name] = [[_day_ms(d), v] for d, v in sorted(days.items())]
    table = re.search(r"<table.*?</table>", html, re.S)
    tickers: list[str] = []
    rows: dict[str, dict] = {}
    if table:
        for tr in re.findall(r"<tr.*?</tr>", table.group(0), re.S):
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
            if not cells:
                continue
            if cells[0] == "Date":
                tickers = [c for c in cells[1:] if c and c != "Totals"]
                continue
            try:
                day = datetime.strptime(cells[0], "%b %d, %Y").date().isoformat()
            except ValueError:
                continue                                    # the Total / Average / Maximum / Minimum rows
            vals = {}
            for t, c in zip(tickers, cells[1:]):
                try:
                    usd_m = float(c.replace(",", ""))
                except ValueError:
                    continue
                vals[t] = round(usd_m * 1e6 / price) if price else None
            rows[day] = vals
    if not any(series.values()) and not rows:
        raise ValueError("bitbo page had no flow data")
    total: dict[int, float] = {}
    for points in series.values():         # the page's own total repeats some funds; add the issuers up instead
        for ms, v in points:
            total[ms] = total.get(ms, 0.0) + v
    return {"tickers": tickers, "by_fund": rows, "price_used": price,
            "total_btc": [[ms, v] for ms, v in sorted(total.items())],
            "issuer_btc": {BITBO_ISSUERS[k]: v for k, v in series.items() if v}}


def parse_cmc_funds(doc: dict) -> dict[str, dict]:
    out = {}
    for e in doc.get("data") or []:
        if e.get("type") != "Spot" or e.get("country") != "us" or not e.get("ticker"):
            continue
        out[e["ticker"]] = {"name": e.get("name"), "aum": e.get("aum"), "market_cap": e.get("marketcap"), "price": e.get("price"),
                            "volume": e.get("volume"), "premium_pct": e.get("premiumDiscount"),
                            "fee_pct": e.get("netFee"), "updated": int(e["updateTime"]) if e.get("updateTime") else None}
    return out


def parse_cmc_flow(doc: dict) -> dict[str, float]:
    """Total net flow per day in USD, keyed by date."""
    return {_iso(int(p["timestamp"])): float(p["value"]) for p in (doc.get("data") or {}).get("points") or []
            if p.get("value") is not None}


def tx_flow(tx: dict, addresses: set[str]) -> dict | None:
    """One transaction's net effect on a set of addresses, or None if it is internal or dust."""
    spent = recv = 0
    ext_in: dict[str, int] = {}
    ext_out: dict[str, int] = {}
    for v in tx.get("vin") or []:
        p = v.get("prevout") or {}
        a = p.get("scriptpubkey_address")
        if a in addresses:
            spent += p.get("value", 0)
        elif a:
            ext_in[a] = ext_in.get(a, 0) + p.get("value", 0)
    for o in tx.get("vout") or []:
        a = o.get("scriptpubkey_address")
        if a in addresses:
            recv += o.get("value", 0)
        elif a:
            ext_out[a] = ext_out.get(a, 0) + o.get("value", 0)
    net = recv - spent
    if abs(net) < DUST_SATS:
        return None
    other = ext_in if net > 0 else ext_out
    status = tx.get("status") or {}
    return {"txid": tx["txid"], "sats": net, "confirmed": bool(status.get("confirmed")),
            "block_time": status.get("block_time"),
            "cp": max(other, key=other.get) if other else None, "cp_n": len(other)}


# ---------- the tracker ----------

class EtfFlows:
    def __init__(self, session: aiohttp.ClientSession, data_dir: Path | None = None, price_at=None):
        self.s = session
        self.dir = Path(data_dir) / "etf" if data_dir else None
        self.user_file = Path(data_dir) / "etf_wallets.json" if data_dir else None
        self.price_at = price_at or (lambda ms: None)     # BTC price at a time, for USD values
        self.wallets: dict[str, dict[str, dict]] = {}     # ticker -> address -> {src, seen, listed, bal, n_tx, dirty}
        self.lists: dict[str, dict] = {}                  # ticker -> {source, published, holdings_btc, ...}
        self.events: dict[str, dict] = {}                 # "TICKER:txid" -> flow
        self.published = {"by_fund": {}, "total_usd": {}}  # kept on disk so history builds up
        self.bitbo: dict = {}
        self.market: dict[str, dict] = {}
        self.status: dict[str, dict] = {}
        self.on_update = None
        self._tasks: list[asyncio.Task] = []
        self.gateways: dict[str, list[str]] = {}          # ticker -> addresses its redemptions are paid to ("redeems_to")
        self._gateways_read: set[str] = set()
        self.adopts: dict[str, str] = {}                  # ticker -> "p2wsh": its spends' P2WSH outputs are its new cold storage
        self._cursor = 0                                  # where the next balance sweep starts, for long lists
        self._user_mtime = None
        self._lists_at = 0.0
        self._host = 0
        self._dirty = False
        self._load()

    # ---------- disk ----------
    def _load(self) -> None:
        if not self.dir:
            return
        try:
            d = json.loads((self.dir / "state.json").read_text())
        except (FileNotFoundError, ValueError):
            return
        self.wallets = d.get("wallets") or {}
        self.lists = d.get("lists") or {}
        self.events = d.get("events") or {}
        self.published.update(d.get("published") or {})

    def _save(self) -> None:
        if not self.dir or not self._dirty:
            return
        cutoff = _now_ms() - KEEP_DAYS * DAY_MS
        self.events = {k: e for k, e in self.events.items() if e["time"] >= cutoff}
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps({"wallets": self.wallets, "lists": self.lists, "events": self.events,
                                   "published": self.published}))
        tmp.replace(self.dir / "state.json")
        self._dirty = False

    # ---------- http ----------
    async def _fetch(self, url: str, params: dict | None = None, as_json: bool = True, timeout: float = 45):
        async with self.s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            r.raise_for_status()
            return await r.json(content_type=None) if as_json else await r.text()

    async def _esplora(self, path: str):
        """mempool.space first; if it refuses, blockstream.info serves the same API."""
        last = None
        for i in range(len(ESPLORA)):
            host = ESPLORA[(self._host + i) % len(ESPLORA)]
            try:
                out = await self._fetch(host + path, timeout=30)
                self._host = (self._host + i) % len(ESPLORA)
                return out
            except Exception as e:
                last = e
        raise last

    def _mark(self, source: str, error: Exception | None = None) -> None:
        was = self.status.get(source, {})
        self.status[source] = {"ok": error is None, "at": _now_ms() if error is None else was.get("at"),
                               "error": "" if error is None else f"{type(error).__name__}: {error}"[:200]}
        if error is not None:
            log.warning("ETF %s failed: %s", source, self.status[source]["error"])

    # ---------- wallet lists ----------
    def _set_list(self, ticker: str, addresses: list[str], src: str, backfill: bool = True) -> int:
        """Make a fund's addresses from one source match a list. Returns how many are new.

        backfill=False is for very long lists: new addresses are watched from now on instead of each
        having its last 30 days read (thousands of requests), and a change to the list re-reads nothing.
        """
        taken = {a for t, ws in self.wallets.items() if t != ticker for a in ws}
        book = self.wallets.setdefault(ticker, {})
        wanted = [a for a in dict.fromkeys(addresses) if ADDRESS.match(a) and a not in taken]
        new = 0
        for a in wanted:
            if a not in book:
                book[a] = {"src": src, "seen": _now_ms(), "listed": True, "bal": None, "n_tx": None, "dirty": backfill}
                new += 1
            elif book[a]["src"] != src and src == "file":
                book[a].update(src=src, listed=True)      # found by itself earlier, now on your list
            else:
                book[a]["listed"] = True
        keep = set(wanted)
        for a, w in list(book.items()):
            if w["src"] != src or a in keep:
                continue
            if src == "file":
                del book[a]                 # your file is the whole truth for what it lists
                new += 1
            else:
                w["listed"] = False         # dropped from the issuer's list: keep watching it empty out
        if new:
            # a longer list can turn an earlier "outflow" into an internal move: re-read the lot
            if backfill:
                for w in book.values():
                    w["dirty"] = True
            self._dirty = True
        if not book:
            self.wallets.pop(ticker, None)
        return new

    async def refresh_wallets(self) -> None:
        try:
            b = parse_bitwise(await self._fetch(BITWISE_URL, as_json=False))
            self._set_list("BITB", [a for a, _, _ in b["wallets"]], "issuer")
            self.lists["BITB"] = {"source": "Bitwise (bitbetf.com)", "published": b["updated"], "kind": "issuer",
                                  "holdings_btc": b["holdings_btc"], "holdings_asof": b["holdings_asof"],
                                  "shares": b["shares"]}
            self._mark("bitwise")
        except Exception as e:
            self._mark("bitwise", e)
        self._read_user_file()

    def _read_user_file(self) -> None:
        if not self.user_file:
            return
        try:
            mtime = self.user_file.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if mtime == self._user_mtime:
            return
        self._user_mtime = mtime
        try:
            doc = json.loads(self.user_file.read_text()) if mtime else {}
            if not isinstance(doc, dict):
                raise ValueError("etf_wallets.json must be an object of ticker -> addresses")
            for ticker in {t for t, ws in self.wallets.items() if any(w["src"] == "file" for w in ws.values())} - \
                    {str(t).upper() for t in doc}:
                self._set_list(ticker, [], "file")
                if self.lists.get(ticker, {}).get("kind") == "file":
                    self.lists.pop(ticker, None)
            gateways, adopts = {}, {}
            for ticker, v in doc.items():
                ticker = str(ticker).upper()
                addrs = v.get("addresses", []) if isinstance(v, dict) else v
                if not isinstance(addrs, list):
                    raise ValueError(f"{ticker}: expected a list of addresses")
                bad = [a for a in addrs if not isinstance(a, str) or not ADDRESS.match(a)]
                self._set_list(ticker, [a for a in addrs if isinstance(a, str)], "file", backfill=len(addrs) <= BIG_LIST)
                gates = v.get("redeems_to", []) if isinstance(v, dict) else []
                gates = [a for a in gates if isinstance(a, str) and ADDRESS.match(a)] if isinstance(gates, list) else []
                if gates:
                    gateways[ticker] = gates
                if isinstance(v, dict) and v.get("adopts") == "p2wsh":
                    adopts[ticker] = "p2wsh"
                if self.lists.get(ticker, {}).get("kind") != "issuer":
                    self.lists[ticker] = {"source": (v.get("source") if isinstance(v, dict) else None) or "etf_wallets.json",
                                          "published": int(mtime * 1000), "kind": "file", "rejected": len(bad),
                                          "redeems_to": gates}
            self.gateways, self.adopts = gateways, adopts
            self._gateways_read &= {g for gs in gateways.values() for g in gs}
            self._mark("wallet_file")
        except Exception as e:
            self._mark("wallet_file", e)

    # ---------- chain ----------
    async def sync_chain(self) -> bool:
        """Read every address's balance; re-read the transactions of the ones that changed."""
        pairs = [(t, a) for t, ws in self.wallets.items() for a in ws]
        changed = False
        sweep = pairs
        if len(pairs) > BALANCE_BATCHES * BATCH:          # a long list: this cycle's share, the rest next time
            start = self._cursor % len(pairs)
            sweep = (pairs + pairs)[start:start + BALANCE_BATCHES * BATCH]
            self._cursor = start + len(sweep)
        for i in range(0, len(sweep), BATCH):
            chunk = sweep[i:i + BATCH]
            data = await self._fetch(BALANCE_API, {"active": "|".join(a for _, a in chunk)})
            for t, a in chunk:
                d, w = data.get(a), self.wallets.get(t, {}).get(a)
                if d is None or w is None:
                    continue
                if w["bal"] != d["final_balance"] or w["n_tx"] != d["n_tx"]:
                    first = w["bal"] is None and not w["dirty"]       # a long list's address, seen for the first time
                    w["bal"], w["n_tx"], w["dirty"] = d["final_balance"], d["n_tx"], not first
                    changed = self._dirty = True
            if i + BATCH < len(sweep):
                await asyncio.sleep(1)
        for t, gates in list(self.gateways.items()):
            for g in gates:
                if await self._read_gateway(t, g):
                    changed = True
        todo = [(t, a) for t, a in pairs if self.wallets.get(t, {}).get(a, {}).get("dirty")][:TX_FETCH_PER_CYCLE]
        for t, a in todo:
            if await self._read_address(t, a):
                changed = True
            await asyncio.sleep(0.25)
        self._mark("chain")
        return changed

    async def _read_gateway(self, ticker: str, gateway: str) -> bool:
        """A fund's redemption address: whatever one address pays into it is that fund's bitcoin leaving.

        The paying address is added to the fund's wallets if it wasn't known, so the flow is recorded even
        when the list is incomplete. The first read goes back a few pages; after that only the newest.
        """
        cutoff = time.time() - HISTORY_DAYS * 86400
        txs = await self._esplora(f"/address/{gateway}/txs")
        if gateway not in self._gateways_read:
            page = [t for t in txs if (t.get("status") or {}).get("confirmed")]
            for _ in range(GATEWAY_PAGES - 1):
                if len(page) < 25 or (page[-1]["status"].get("block_time") or 0) < cutoff:
                    break
                page = await self._esplora(f"/address/{gateway}/txs/chain/{page[-1]['txid']}")
                txs += page
            self._gateways_read.add(gateway)
        taken = {a for t, ws in self.wallets.items() if t != ticker for a in ws}
        book = self.wallets.setdefault(ticker, {})
        changed = False
        for tx in txs:
            bt = (tx.get("status") or {}).get("block_time")
            if bt and bt < cutoff:
                continue
            payers = {(v.get("prevout") or {}).get("scriptpubkey_address") for v in tx.get("vin") or []}
            pays_in = any(o.get("scriptpubkey_address") == gateway for o in tx.get("vout") or [])
            if not pays_in or gateway in payers or len(payers) != 1:
                continue                                   # the gateway's own sweep, or not one wallet paying in
            payer = next(iter(payers))
            if not payer or payer in taken:
                continue
            if payer not in book:
                book[payer] = {"src": "seen", "seen": _now_ms(), "listed": False, "bal": None, "n_tx": None, "dirty": False}
                self._dirty = changed = True
            changed |= self._record(ticker, tx, set(book))
        return changed

    async def _read_address(self, ticker: str, address: str) -> bool:
        w = self.wallets[ticker][address]
        cutoff = time.time() - HISTORY_DAYS * 86400
        txs = await self._esplora(f"/address/{address}/txs")
        page = [t for t in txs if (t.get("status") or {}).get("confirmed")]
        for _ in range(TX_PAGES - 1):
            if len(page) < 25 or (page[-1]["status"].get("block_time") or 0) < cutoff:
                break
            page = await self._esplora(f"/address/{address}/txs/chain/{page[-1]['txid']}")
            txs += page
        mine = set(self.wallets[ticker])
        changed = False
        for tx in txs:
            bt = (tx.get("status") or {}).get("block_time")
            if bt and bt < cutoff:
                continue
            changed |= self._record(ticker, tx, mine)
        w["dirty"] = False
        self._dirty = True
        return changed

    def _adopt(self, ticker: str, tx: dict, mine: set[str]) -> set[str]:
        """For a fund whose custodian sweeps into fresh multisig addresses (Fidelity): when most of a
        transaction's inputs are the fund's, its P2WSH outputs are the fund's new cold storage, not an outflow."""
        vin = [v.get("prevout") or {} for v in tx.get("vin") or []]
        ours = sum(p.get("value", 0) for p in vin if p.get("scriptpubkey_address") in mine)
        if not ours or ours * 2 < sum(p.get("value", 0) for p in vin):
            return mine
        book, added = self.wallets.setdefault(ticker, {}), set()
        for o in tx.get("vout") or []:
            a = o.get("scriptpubkey_address")
            if a and a.startswith("bc1q") and len(a) == 62 and a not in mine \
                    and not any(a in ws for t, ws in self.wallets.items() if t != ticker):
                book[a] = {"src": "seen", "seen": _now_ms(), "listed": False, "bal": None, "n_tx": None, "dirty": True}
                added.add(a)
        if added:
            self._dirty = True
        return mine | added

    def _record(self, ticker: str, tx: dict, mine: set[str]) -> bool:
        key = f"{ticker}:{tx['txid']}"
        old = self.events.get(key)
        if self.adopts.get(ticker) == "p2wsh":
            mine = self._adopt(ticker, tx, mine)
        f = tx_flow(tx, mine)
        if f is None:
            if old:                      # it was a flow until the list grew: now an internal move
                del self.events[key]
                return True
            return False
        now = _now_ms()
        seen = old["seen"] if old else now
        t = f["block_time"] * 1000 if f["block_time"] else seen
        px = (old or {}).get("px") or self.price_at(t)
        ev = {"ticker": ticker, "txid": f["txid"], "time": t, "seen": seen, "btc": f["sats"] / SATS,
              "px": px, "usd": f["sats"] / SATS * px if px else None, "confirmed": f["confirmed"],
              "cp": f["cp"], "cp_n": f["cp_n"],
              "live": old["live"] if old else (not f["confirmed"] or now - t < 2 * 3600_000)}
        if ev == old:
            return False
        self.events[key] = ev
        return True

    # ---------- published daily numbers ----------
    async def refresh_published(self) -> None:
        try:
            funds, flow = await asyncio.gather(self._fetch(f"{CMC_API}/list", {"category": "btc"}),
                                               self._fetch(f"{CMC_API}/overview/netflow/chart",
                                                           {"category": "btc", "range": "30d"}))
            self.market = parse_cmc_funds(funds)
            self.published["total_usd"].update(parse_cmc_flow(flow))
            self._dirty = True
            self._mark("coinmarketcap")
        except Exception as e:
            self._mark("coinmarketcap", e)
        try:
            b = parse_bitbo(await self._fetch(BITBO_URL, as_json=False, timeout=60))
            self.bitbo = b
            self.published["by_fund"].update(b["by_fund"])
            self._dirty = True
            self._mark("bitbo")
        except Exception as e:
            self._mark("bitbo", e)

    # ---------- loops ----------
    async def _loop(self, name: str, step, seconds: int) -> None:
        while True:
            try:
                changed = await step()
                self._save()
                if changed is not False and self.on_update:
                    await self.on_update(self.snapshot(compact=True))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._mark(name, e)
            await asyncio.sleep(seconds)

    async def _chain_step(self):
        if time.time() - self._lists_at >= WALLET_SECONDS:
            self._lists_at = time.time()
            await self.refresh_wallets()
        return await self.sync_chain()

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._loop("chain", self._chain_step, CHAIN_SECONDS)),
                       asyncio.create_task(self._loop("published", self.refresh_published, PUBLISHED_SECONDS))]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._save()

    # ---------- outputs ----------
    def _event_out(self, e: dict) -> dict:
        lst = self.lists.get(e["ticker"], {})
        # An outflow newer than the issuer's last published list may be a move to a new wallet of the
        # same fund that isn't on the list yet. It is settled once the list is republished.
        unsettled = e["btc"] < 0 and lst.get("kind") == "issuer" and e["time"] > (lst.get("published") or 0)
        return {**e, "unsettled": unsettled}

    def _window(self, events: list[dict], since: int) -> dict:
        ev = [e for e in events if e["time"] >= since]
        usd = [e["usd"] for e in ev if e["usd"] is not None]
        return {"in": sum(e["btc"] for e in ev if e["btc"] > 0), "out": -sum(e["btc"] for e in ev if e["btc"] < 0),
                "net": sum(e["btc"] for e in ev), "net_usd": sum(usd) if usd else None, "n": len(ev)}

    def _tickers(self) -> list[str]:
        known = [f["ticker"] for f in FUNDS]
        extra = sorted({*self.market, *self.wallets, *(t for r in self.published["by_fund"].values() for t in r)} - set(known),
                       key=lambda t: -(self.market.get(t, {}).get("aum") or 0))
        return known + extra

    def snapshot(self, compact: bool = False) -> dict:
        now = _now_ms()
        today = now - now % DAY_MS
        price = self.price_at(now)
        close_price = self.price_at(_last_us_close(now)) or price    # assets are as of the close, so divide by that price
        events = sorted(self.events.values(), key=lambda e: -e["time"])
        by_fund = self.published["by_fund"]
        days = sorted(by_fund)
        meta = {f["ticker"]: f for f in FUNDS}
        funds = []
        for t in self._tickers():
            m, mk, ws, lst = meta.get(t, {}), self.market.get(t, {}), self.wallets.get(t, {}), self.lists.get(t, {})
            ev = [e for e in events if e["ticker"] == t]
            known = [w["bal"] for w in ws.values() if w["bal"] is not None]
            tracked = sum(known) / SATS if known else None
            holdings = lst.get("holdings_btc")
            estimated = False
            # no published holdings: shares x share price (it follows the fund's bitcoin closely) over the BTC close
            worth = mk.get("market_cap") or mk.get("aum")
            if holdings is None and worth and close_price:
                holdings, estimated = worth / close_price, True
            # the latest non-zero day: a zero on the newest day usually means "not reported yet"
            last = next(({"date": d, "btc": by_fund[d][t]} for d in reversed(days) if by_fund[d].get(t)), None)
            funds.append({
                "ticker": t, "name": m.get("name") or mk.get("name") or t, "issuer": m.get("issuer"),
                "custodian": m.get("custodian"),
                "wallets": lst.get("kind", "file") if ws else "private",
                "wallet_source": lst.get("source"), "list_published": lst.get("published"),
                "addresses": len(ws), "addresses_funded": sum(1 for w in ws.values() if w["bal"]),
                "syncing": sum(1 for w in ws.values() if w.get("dirty")),
                "tracked_btc": tracked, "holdings_btc": holdings, "holdings_estimated": estimated,
                "coverage_pct": tracked / holdings * 100 if tracked is not None and holdings else None,
                "aum": mk.get("aum"), "share_price": mk.get("price"), "volume": mk.get("volume"),
                "premium_pct": mk.get("premium_pct"),
                "onchain": {"today": self._window(ev, today), "h24": self._window(ev, now - DAY_MS),
                            "d7": self._window(ev, now - 7 * DAY_MS), "d30": self._window(ev, now - 30 * DAY_MS)} if ws else None,
                "published_last": last,
                "published_5d": sum(by_fund[d].get(t) or 0 for d in days[-5:]) if days else None,
            })
        tracked_funds = [f for f in funds if f["onchain"]]
        total_usd = self.published["total_usd"]
        usd_days = sorted(total_usd)
        out = {
            "ready": bool(self.status or self.wallets), "updated": now, "price": price,
            "funds": funds,
            "totals": {
                "tracked": [f["ticker"] for f in tracked_funds],
                "untracked": [f["ticker"] for f in funds if not f["onchain"]],
                "tracked_btc": sum(f["tracked_btc"] or 0 for f in tracked_funds),
                "aum": sum(f["aum"] or 0 for f in funds) or None,
                "onchain": {k: self._window(events, s) for k, s in
                            (("today", today), ("h24", now - DAY_MS), ("d7", now - 7 * DAY_MS), ("d30", now - 30 * DAY_MS))},
                "published_usd_last": {"date": usd_days[-1], "usd": total_usd[usd_days[-1]]} if usd_days else None,
                "published_usd_5d": sum(total_usd[d] for d in usd_days[-5:]) if usd_days else None,
                "published_btc_date": days[-1] if days else None,
            },
            "events": [self._event_out(e) for e in events[:60 if compact else 500]],
            "sources": self.status,
        }
        if not compact:
            daily: dict[str, dict[str, list]] = {}
            for e in events:
                if e["time"] < now - HISTORY_DAYS * DAY_MS:
                    continue
                row = daily.setdefault(e["ticker"], {}).setdefault(_iso(e["time"]), [0.0, 0.0])
                row[0 if e["btc"] > 0 else 1] += abs(e["btc"])
            out["onchain_daily"] = {t: [[_day_ms(d), v[0], v[1]] for d, v in sorted(rows.items())]
                                    for t, rows in daily.items()}
            out["published"] = {
                "tickers": [t for t in self._tickers() if any(t in by_fund[d] for d in days)],
                "by_fund": [[d, by_fund[d]] for d in days[-60:]],
                "total_usd": [[_day_ms(d), total_usd[d]] for d in usd_days],
                "total_btc": self.bitbo.get("total_btc", []),
                "issuer_btc": self.bitbo.get("issuer_btc", {}),
            }
        return out

    def wallet_list(self, ticker: str) -> dict:
        ws = self.wallets.get(ticker.upper(), {})
        return {"ticker": ticker.upper(), **self.lists.get(ticker.upper(), {}),
                "wallets": sorted(({"address": a, "btc": w["bal"] / SATS if w["bal"] is not None else None,
                                    "n_tx": w["n_tx"], "source": w["src"], "listed": w["listed"], "first_seen": w["seen"]}
                                   for a, w in ws.items()), key=lambda r: -(r["btc"] or 0))}

    def context(self) -> list[str]:
        """Plain lines for the Claude chat."""
        s = self.snapshot(compact=True)
        if not s["ready"]:
            return []
        b = lambda v: "n/a" if v is None else f"{v:+,.0f} BTC"
        usd = lambda v: "n/a" if v is None else f"{'-' if v < 0 else '+'}${abs(v) / 1e6:,.1f}M"
        t = s["totals"]
        out = ["### Spot Bitcoin ETF flows"]
        if t["published_usd_last"]:
            out.append(f"Published total net flow (CoinMarketCap): {usd(t['published_usd_last']['usd'])} on "
                       f"{t['published_usd_last']['date']}; last 5 trading days {usd(t['published_usd_5d'])}.")
        rows = [f for f in s["funds"] if f["published_last"]]
        if rows:
            out.append(f"Bitcoin added or removed per fund (bitbo.io, from holdings; the table runs to {t['published_btc_date']}, "
                       "and its newest row can be incomplete). Last move, then the last 5 table days:")
            out += [f"- {f['ticker']}: {b(f['published_last']['btc'])} on {f['published_last']['date']}; 5d {b(f['published_5d'])}"
                    + (f"; assets ${f['aum'] / 1e9:,.1f}B" if f["aum"] else "") for f in rows]
        if t["tracked"]:
            out.append(f"On-chain, wallets watched live for {', '.join(t['tracked'])} only "
                       f"({t['tracked_btc']:,.0f} BTC). Other issuers keep their addresses private.")
            for f in s["funds"]:
                if f["onchain"]:
                    o = f["onchain"]
                    out.append(f"- {f['ticker']} on-chain net: 24h {b(o['h24']['net'])}, 7d {b(o['d7']['net'])}, "
                               f"30d {b(o['d30']['net'])}; {f['addresses']} addresses"
                               + (f", {f['coverage_pct']:.1f}% of holdings" if f["coverage_pct"] is not None else ""))
            for e in s["events"][:8]:
                out.append(f"  {datetime.fromtimestamp(e['time'] / 1000, timezone.utc):%m-%d %H:%M} UTC {e['ticker']} "
                           f"{b(e['btc'])}{'' if e['confirmed'] else ' (unconfirmed)'}"
                           f"{' (may be a move to a new wallet of the fund)' if e['unsettled'] else ''}")
        out.append("On-chain moves settle creations and redemptions a business day or two after the trade date.")
        return out
