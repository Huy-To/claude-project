"""Batched address lookups on blockchain.info's public API: balances (100 addresses per request) and
full histories for many quiet addresses in one request. Gentle: one request at a time with a pause."""
import json, time, urllib.parse, urllib.request, urllib.error

PAUSE = 2.5
_last = [0.0]
calls = [0]


def _get(url):
    for attempt in range(6):
        wait = PAUSE - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        calls[0] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "python-urllib"}), timeout=90) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 502):
                time.sleep(45 * (attempt + 1))
            else:
                raise RuntimeError(f"HTTP {e.code} {e.read()[:200]!r}")
        except Exception:
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("blockchain.info kept failing")


def balances(addrs):
    out = {}
    addrs = list(dict.fromkeys(addrs))
    for i in range(0, len(addrs), 100):
        out.update(_get("https://blockchain.info/balance?" + urllib.parse.urlencode({"active": "|".join(addrs[i:i + 100])})))
    return out


def _norm(t):
    return {"txid": t["hash"], "time": t.get("time"), "height": t.get("block_height"),
            "vin": [((i.get("prev_out") or {}).get("addr"), (i.get("prev_out") or {}).get("value", 0)) for i in t["inputs"]],
            "vout": [(o.get("addr"), o.get("value", 0)) for o in t["out"]]}


def histories(addrs, n_tx=None, cap=400, budget=90, most=45):
    """{address: [tx, ...]} newest first. Addresses with more than `cap` transactions come back truncated
    (their list holds the newest `cap`); n_tx gives the true count."""
    addrs = list(dict.fromkeys(addrs))
    n_tx = dict(n_tx or {})
    missing = [a for a in addrs if a not in n_tx]
    if missing:
        for a, d in balances(missing).items():
            n_tx[a] = d["n_tx"]
    out = {a: [] for a in addrs}
    quiet = sorted((a for a in addrs if 0 < n_tx.get(a, 0) <= budget), key=lambda a: n_tx[a])
    batch, load = [], 0
    groups = []
    for a in quiet:
        if batch and (load + n_tx[a] > budget or len(batch) >= most):
            groups.append(batch)
            batch, load = [], 0
        batch.append(a)
        load += n_tx[a]
    if batch:
        groups.append(batch)
    for g in groups:
        doc = _get("https://blockchain.info/multiaddr?" + urllib.parse.urlencode({"active": "|".join(g), "n": 100}))
        members = set(g)
        for t in doc.get("txs", []):
            tx = _norm(t)
            for a in {x for x, _ in tx["vin"]} | {x for x, _ in tx["vout"]}:
                if a in members:
                    out[a].append(tx)
    for a in addrs:
        if n_tx.get(a, 0) > budget:
            got, off = [], 0
            while off < min(n_tx[a], cap):
                doc = _get("https://blockchain.info/multiaddr?" + urllib.parse.urlencode({"active": a, "n": 100, "offset": off}))
                page = [_norm(t) for t in doc.get("txs", [])]
                got += page
                off += 100
                if len(page) < 100:
                    break
            out[a] = got
    return out, n_tx
