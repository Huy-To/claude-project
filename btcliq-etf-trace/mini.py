"""Grayscale Bitcoin Mini Trust: forward closure of its custody addresses.
Seeds: the 700-BTC outputs of the 2024-07-30/31 spin-off peel chain, the June 2025 re-keying outputs
(marked with 0.001 BTC to the spin-off staging address), and every payer of Mini's two redemption
addresses. A spend by a Mini address sends its non-external outputs (change, rotations) to Mini."""
import sys, json, collections
sys.path.insert(0, ".")
import mp

GATE = {"3B1g33p9mWS1YJB57G5ewuSXkV6vQ5cXKz", "3Dwfb4U5oXgKQVrxE6WH6GXgXqf4PUYaWW"}
STAGE = "3GMdKrRRmto6hNHVEoLA6xTy6xsmU5PyNH"
HELP = "3J7cUjBZxvGRCwFBz3q23zAsnhFfZrDSSU"
FEE = "3EXBmHg9bLabEQihaELPSBwngVmyAVjCrr"
HOT = "3MqUP6G1daVS5YTD8fz3QgwjZortWwxXFd"
EXT = GATE | {STAGE, HELP, FEE, HOT}

why = {}
for d, txid, a, v in json.load(open("peels.json")):
    if abs(v - 700) < 0.01:
        why[a] = f"spin-off 2024-07-31: 700 BTC from GBTC via {txid[:12]}"
for t in mp.txs(STAGE):
    if mp.day(t) >= "2025-06-01" and not any(v["prevout"]["scriptpubkey_address"] == STAGE for v in t["vin"]):
        for o in t["vout"]:
            a = o.get("scriptpubkey_address")
            if a not in EXT:
                why.setdefault(a, f"re-keyed {mp.day(t)} with the 0.001 BTC marker to {STAGE[:10]} ({t['txid'][:12]})")
        for v in t["vin"]:
            why.setdefault(v["prevout"]["scriptpubkey_address"], f"re-keyed {mp.day(t)} (input of {t['txid'][:12]})")
for g in GATE:
    for t in mp.txs(g, maxpages=10):
        if any(v["prevout"]["scriptpubkey_address"] == g for v in t["vin"]):
            continue
        for v in t["vin"]:
            why.setdefault(v["prevout"]["scriptpubkey_address"], f"paid Mini redemption address {g[:10]} ({mp.day(t)}, {t['txid'][:12]})")

queue = list(why)
done = set()
while queue:
    a = queue.pop()
    if a in done or a in EXT:
        continue
    done.add(a)
    for t in mp.txs(a):
        ins = [v["prevout"]["scriptpubkey_address"] for v in t["vin"]]
        if a not in ins:
            continue
        if any(x in GATE for x in ins) or len(set(ins)) != 1:
            continue
        cand = [(o.get("scriptpubkey_address"), o["value"]) for o in t["vout"] if o.get("scriptpubkey_address") not in EXT]
        top = max((v for _, v in cand), default=0)
        for b, v in cand:
            if not (v == top or (v % (50 * 10**8) == 0 and v > 0)):
                continue
            if b and b not in why:
                why[b] = f"change/rotation from Mini address {a[:12]} ({mp.day(t)}, {t['txid'][:12]})"
                queue.append(b)
    if len(done) % 50 == 0:
        print(len(done), len(why), len(queue), flush=True)
bal = {}
for a in why:
    s = mp.addr(a)["chain_stats"]
    bal[a] = (s["funded_txo_sum"] - s["spent_txo_sum"]) / 1e8
json.dump({"why": why, "bal": bal}, open("mini.json", "w"), indent=1)
held = {a: b for a, b in bal.items() if b > 0.01}
print("addresses", len(why), "holding", len(held), "BTC", round(sum(held.values()), 2))
print(collections.Counter(round(b) for b in held.values()).most_common(15))
