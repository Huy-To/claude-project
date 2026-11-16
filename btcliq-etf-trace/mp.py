"""Cached, gentle mempool.space client: address txs (all pages), tx outspends, summaries."""
import json, os, sys, time, hashlib, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://mempool.space/api"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE, exist_ok=True)
_last = [0.0]
PAUSE = 0.6


def get(path, cache=True):
    key = os.path.join(CACHE, hashlib.sha1(path.encode()).hexdigest() + ".json")
    if cache and os.path.exists(key):
        return json.load(open(key))
    for attempt in range(8):
        w = PAUSE - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "curl/8"}), timeout=60) as r:
                d = json.loads(r.read())
            if cache:
                json.dump(d, open(key, "w"))
            return d
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504):
                time.sleep(15 * (attempt + 1)); continue
            raise
        except Exception:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("mempool failing: " + path)


def addr(a, cache=True):
    return get(f"/address/{a}", cache)


def txs(a, maxpages=40):
    """All confirmed txs of an address, newest first."""
    out = get(f"/address/{a}/txs/chain")
    while len(out) and len(out) % 25 == 0 and maxpages > 0:
        page = get(f"/address/{a}/txs/chain/{out[-1]['txid']}")
        if not page:
            break
        out += page
        maxpages -= 1
    return out


def outspends(txid):
    return get(f"/tx/{txid}/outspends")


def tx(txid):
    return get(f"/tx/{txid}")


def day(t):
    bt = t.get("status", {}).get("block_time")
    return datetime.fromtimestamp(bt, timezone.utc).strftime("%Y-%m-%d") if bt else "mempool"


def summ(t):
    ins = [(v["prevout"]["scriptpubkey_address"] if v.get("prevout") else None, v["prevout"]["value"] / 1e8 if v.get("prevout") else 0) for v in t["vin"]]
    outs = [(o.get("scriptpubkey_address"), o["value"] / 1e8) for o in t["vout"]]
    return ins, outs


def show(t, mark=()):
    ins, outs = summ(t)
    print(f"{day(t)} {t['txid'][:12]} in={len(ins)} {sum(v for _, v in ins):.4f}  ->  " +
          ", ".join(f"{'*' if a in mark else ''}{(a or '?')[:14]}:{v:.4f}" for a, v in outs[:8]) + (" ..." if len(outs) > 8 else ""))
