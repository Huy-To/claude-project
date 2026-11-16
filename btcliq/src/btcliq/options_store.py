"""Recorder for the option chain: every chain snapshot goes into SQLite, one file per month.

Files: <data-dir>/options/YYYY-MM.sqlite (UTC month of the snapshot). Read them with plain
`sqlite3`; the view `quotes_v` gives every row in real units:

    sqlite3 data/options/2026-11.sqlite \
      "SELECT datetime(ts/1000,'unixepoch'), bid, ask, mark, iv FROM quotes_v
       WHERE inst_id='BTC-USD-261127-75000-C' ORDER BY ts"

Tables (numbers are stored as small integers to keep the files compact):

    contracts(id, inst_id, family, expiry, exp_ms, strike, type, ct_mult, tick)
    snaps(ts, family, index_px)        one row per chain read; ts in ms
    fwds(ts, family, expiry, fwd)      the forward is the same for every contract of an expiry
    quotes(contract_id, ts,
           bid, ask          price in ticks              (price = n * contracts.tick)
           mark              price in 1/1000 of a tick   (mark is finer than the tick)
           bid_sz, ask_sz, oi  whole contracts
           iv, iv_bid, iv_ask, delta   basis points      (value = n / 10000))

Samples arrive every 60 s for both families, and every ~10 s for the family open in the
Options tab. `thin()` keeps the last sample of each minute once samples are a day old.

Rows are kept in time order (key: ts, contract_id), so a snapshot is written as one block of
a few pages. Keyed by contract first, every snapshot would rewrite a page per contract:
gigabytes of disk writes a day. One contract's series is still quick to read, because the
small `snaps` table lists the timestamps to look up; `quotes_v` and `read()` both go that way.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("btcliq")

MARK_SCALE = 1000        # mark is stored in 1/1000 of a tick
BPS = 10_000             # IV and delta are stored in basis points
MINUTE_MS = 60_000
DAY_MS = 86_400_000
_MONTH_FILE = re.compile(r"\d{4}-\d{2}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts(
  id INTEGER PRIMARY KEY, inst_id TEXT NOT NULL, family TEXT NOT NULL, expiry TEXT NOT NULL,
  exp_ms INTEGER NOT NULL, strike REAL NOT NULL, type TEXT NOT NULL, ct_mult REAL, tick REAL NOT NULL,
  UNIQUE(inst_id, tick));
CREATE TABLE IF NOT EXISTS snaps(
  ts INTEGER NOT NULL, family TEXT NOT NULL, index_px REAL,
  PRIMARY KEY(ts, family)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fwds(
  ts INTEGER NOT NULL, family TEXT NOT NULL, expiry TEXT NOT NULL, fwd REAL,
  PRIMARY KEY(ts, family, expiry)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS quotes(
  contract_id INTEGER NOT NULL, ts INTEGER NOT NULL,
  bid INTEGER, ask INTEGER, mark INTEGER, bid_sz INTEGER, ask_sz INTEGER,
  iv INTEGER, iv_bid INTEGER, iv_ask INTEGER, delta INTEGER, oi INTEGER,
  PRIMARY KEY(ts, contract_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value) WITHOUT ROWID;
CREATE VIEW IF NOT EXISTS quotes_v AS
  SELECT q.ts, c.inst_id, c.family, c.expiry, c.exp_ms, c.strike, c.type,
         q.bid * c.tick AS bid, q.ask * c.tick AS ask, q.mark * c.tick / 1000.0 AS mark,
         q.bid_sz, q.ask_sz,
         q.iv / 10000.0 AS iv, q.iv_bid / 10000.0 AS iv_bid, q.iv_ask / 10000.0 AS iv_ask,
         q.delta / 10000.0 AS delta, q.oi, s.index_px, f.fwd
  FROM contracts c
  CROSS JOIN snaps s ON s.family = c.family          -- CROSS JOIN fixes the order: contract, time, quote
  CROSS JOIN quotes q ON q.ts = s.ts AND q.contract_id = c.id
  LEFT JOIN fwds f ON f.ts = s.ts AND f.family = c.family AND f.expiry = c.expiry;
"""


def month_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m")


def _scaled(v, scale: float) -> int | None:
    """v * scale as an integer; None stays None, and so does anything that isn't a finite number."""
    if v is None:
        return None
    try:
        x = float(v) * scale
    except (TypeError, ValueError):
        return None
    return round(x) if math.isfinite(x) else None


def _decimals(tick: float) -> int:
    """Decimal places needed to write a multiple of this tick (0.0001 -> 4, 5 -> 0)."""
    return max(0, -math.floor(math.log10(tick) + 1e-9)) if tick > 0 else 8


class OptionStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()          # one writer at a time; readers use their own connections
        self._con: sqlite3.Connection | None = None
        self._month: str | None = None
        self._ids: dict[tuple[str, float], int] = {}   # (inst_id, tick) -> contracts.id in the open file

    # ---------- files ----------
    def path(self, month: str) -> Path:
        return self.root / f"{month}.sqlite"

    def months(self) -> list[str]:
        """Months that have a file, oldest first."""
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.sqlite") if _MONTH_FILE.fullmatch(p.stem))

    def _open(self, month: str) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path(month), timeout=30, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(SCHEMA)
        return con

    def _writer(self, month: str) -> sqlite3.Connection:
        """The connection for this month's file (call with the lock held)."""
        if self._con is None or self._month != month:
            if self._con is not None:
                self._con.close()
            self._con, self._month = self._open(month), month
            self._ids = {(n, t): i for i, n, t in self._con.execute("SELECT id, inst_id, tick FROM contracts")}
        return self._con

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
            self._con, self._month, self._ids = None, None, {}

    # ---------- writing ----------
    def record(self, snap: dict) -> int:
        """Store one chain snapshot (the dict OptionsFeed.chain returns) in one transaction.
        Returns the number of contract rows written."""
        ts, family = int(snap["ts"]), snap["family"]
        with self._lock:
            con = self._writer(month_of(ts))
            new, rows, fwds = [], [], {}
            for c in snap["contracts"]:
                tick = c.get("tick")
                if not tick or tick <= 0:
                    continue
                vals = (
                    _scaled(c.get("bid"), 1 / tick), _scaled(c.get("ask"), 1 / tick),
                    _scaled(c.get("mark"), MARK_SCALE / tick),
                    _scaled(c.get("bid_sz"), 1), _scaled(c.get("ask_sz"), 1),
                    _scaled(c.get("iv"), BPS), _scaled(c.get("iv_bid"), BPS), _scaled(c.get("iv_ask"), BPS),
                    _scaled(c.get("delta"), BPS), _scaled(c.get("oi"), 1),
                )
                if all(v is None for v in vals):
                    continue
                if (c["name"], tick) not in self._ids:
                    new.append(c)
                rows.append((c["name"], tick, vals))
                if c.get("fwd") is not None:
                    fwds[c["expiry"]] = c["fwd"]
            with con:  # one transaction per snapshot
                for c in new:
                    cur = con.execute(
                        "INSERT OR IGNORE INTO contracts(inst_id, family, expiry, exp_ms, strike, type, ct_mult, tick) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (c["name"], c["family"], c["expiry"], c["exp_ms"], c["strike"], c["type"],
                         c.get("ct_mult"), c["tick"]))
                    self._ids[(c["name"], c["tick"])] = cur.lastrowid if cur.rowcount else con.execute(
                        "SELECT id FROM contracts WHERE inst_id=? AND tick=?", (c["name"], c["tick"])).fetchone()[0]
                con.execute("INSERT OR REPLACE INTO snaps VALUES(?,?,?)", (ts, family, snap.get("index")))
                con.executemany("INSERT OR REPLACE INTO fwds VALUES(?,?,?,?)",
                                [(ts, family, e, f) for e, f in fwds.items()])
                con.executemany("INSERT OR REPLACE INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                [(self._ids[(n, t)], ts, *v) for n, t, v in rows])
            return len(rows)

    # ---------- reading ----------
    def _months_between(self, since_ms: int, until_ms: int) -> list[str]:
        lo, hi = month_of(max(0, since_ms)), month_of(until_ms)
        return [m for m in self.months() if lo <= m <= hi]

    def read(self, inst_id: str, since_ms: int = 0, until_ms: int | None = None, bucket_ms: int = 0) -> list[dict]:
        """One contract's recorded quotes in real units, oldest first:
        [{"t": ms, "mark", "bid", "ask", "iv"}]. With bucket_ms, only the family's last
        snapshot of each bucket (ts // bucket_ms) is returned."""
        until_ms = int(time.time() * 1000) if until_ms is None else until_ms
        # The snapshot times come from the small snaps table; each is then one key lookup in quotes.
        stamps = ("SELECT max(ts) FROM snaps WHERE family=? AND ts>=? AND ts<=? GROUP BY ts/?" if bucket_ms
                  else "SELECT ts FROM snaps WHERE family=? AND ts>=? AND ts<=?")
        sql = f"SELECT ts, bid, ask, mark, iv FROM quotes WHERE contract_id=? AND ts IN ({stamps})"
        out: list[dict] = []
        for month in self._months_between(since_ms, until_ms):
            con = sqlite3.connect(self.path(month), timeout=10)
            try:
                for cid, tick, family in con.execute(
                        "SELECT id, tick, family FROM contracts WHERE inst_id=?", (inst_id,)).fetchall():
                    d = _decimals(tick)
                    args = (cid, family, since_ms, until_ms) + ((bucket_ms,) if bucket_ms else ())
                    for ts, bid, ask, mark, iv in con.execute(sql, args):
                        out.append({
                            "t": ts,
                            "mark": None if mark is None else round(mark * tick / MARK_SCALE, d + 3),
                            "bid": None if bid is None else round(bid * tick, d),
                            "ask": None if ask is None else round(ask * tick, d),
                            "iv": None if iv is None else iv / BPS,
                        })
            except sqlite3.DatabaseError as e:
                log.warning("options recorder: couldn't read %s: %s", self.path(month).name, e)
            finally:
                con.close()
        out.sort(key=lambda r: r["t"])
        return out

    def contract(self, inst_id: str) -> dict | None:
        """What the recorder knows about a contract (newest file first), e.g. after it expired."""
        for month in reversed(self.months()):
            con = sqlite3.connect(self.path(month), timeout=10)
            try:
                row = con.execute(
                    "SELECT inst_id, family, expiry, exp_ms, strike, type, ct_mult, tick FROM contracts "
                    "WHERE inst_id=? ORDER BY id DESC LIMIT 1", (inst_id,)).fetchone()
            except sqlite3.DatabaseError:
                row = None
            finally:
                con.close()
            if row:
                return dict(zip(("name", "family", "expiry", "exp_ms", "strike", "type", "ct_mult", "tick"), row))
        return None

    # ---------- thinning ----------
    def thin(self, now_ms: int | None = None, older_ms: int = DAY_MS, batch: int = 200) -> int:
        """Keep the last sample of each minute for samples older than `older_ms`.
        Returns the number of quote rows removed. Safe to run as often as you like: each file
        remembers how far it has been thinned."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        cutoff = (now_ms - older_ms) // MINUTE_MS * MINUTE_MS   # whole minutes, so a minute is never split
        removed = 0
        for month in self.months():
            if month > month_of(cutoff):
                break
            removed += self._thin_file(month, cutoff, batch)
        return removed

    def _thin_file(self, month: str, cutoff: int, batch: int) -> int:
        own: sqlite3.Connection | None = None   # a connection of our own when it isn't the writer's month

        def con() -> sqlite3.Connection:
            """This month's connection (call with the lock held). The writer's if it is on this
            month, else one we open and close ourselves, also when the month rolls over meanwhile."""
            nonlocal own
            if own is None and self._con is not None and self._month == month:
                return self._con
            if own is None:
                own = self._open(month)
            return own

        removed = 0
        try:
            with self._lock:
                row = con().execute("SELECT value FROM meta WHERE key='thinned_to'").fetchone()
                start = int(row[0]) if row else 0
                if start >= cutoff:
                    return 0
                # A snapshot goes when a later one exists for the same family in the same minute.
                doomed = con().execute(
                    "SELECT s.ts, s.family FROM snaps s WHERE s.ts>=? AND s.ts<? AND EXISTS ("
                    "  SELECT 1 FROM snaps n WHERE n.family=s.family AND n.ts>s.ts "
                    "  AND n.ts < (s.ts/60000 + 1)*60000)", (start, cutoff)).fetchall()
            by_family: dict[str, list[int]] = {}
            for ts, family in doomed:
                by_family.setdefault(family, []).append(ts)
            for family, stamps in by_family.items():
                for i in range(0, len(stamps), batch):
                    part = stamps[i:i + batch]
                    marks = ",".join("?" * len(part))
                    with self._lock:  # released between batches so recording never waits long
                        c = con()
                        with c:
                            removed += c.execute(
                                f"DELETE FROM quotes WHERE ts IN ({marks}) "
                                f"AND contract_id IN (SELECT id FROM contracts WHERE family=?)", (*part, family)).rowcount
                            c.execute(f"DELETE FROM fwds WHERE family=? AND ts IN ({marks})", (family, *part))
                            c.execute(f"DELETE FROM snaps WHERE family=? AND ts IN ({marks})", (family, *part))
            with self._lock:
                c = con()
                with c:
                    c.execute("INSERT OR REPLACE INTO meta VALUES('thinned_to', ?)", (cutoff,))
        finally:
            if own is not None:
                own.close()
        return removed
