"""IBIT step 3: classify candidate addresses.
  confirmed  - has paid IBIT's redemption deposit address (36YZXc...)
  linked     - rotated from / into a confirmed or linked address (same wallet moving a slot to a new address)
  other      - has paid some other destination and has no IBIT link (another Coinbase Custody client using 300-BTC slots)
  untouched  - a 300-BTC slot filled by a Coinbase Prime hot wallet and never spent
Writes ibit_result.json."""
import json, collections, datetime

T = "/home/miller/.claude/jobs/d18f685a/tmp"
DEP = "36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv"
c = json.load(open(f"{T}/ibit_candidates.json"))
red = set(c["redeemers"])
rows = {}
for l in open(f"{T}/ibit_slots.jsonl"):
    r = json.loads(l)
    rows[r["a"]] = r
rich = {}
for line in open(f"{T}/rich.tsv"):
    a, v = line.split("\t")
    rich[a] = int(v) / 1e8
fund_count = collections.Counter(x for r in rows.values() for x in r["fund"])
hot = {a for a, n in fund_count.items() if n >= 8 and not (a.startswith("bc1q") and len(a) < 50 and a in rows)}
print("hot-wallet funders (fund >= 8 candidates):", len(hot), [(a[:12], fund_count[a]) for a in sorted(hot, key=lambda a: -fund_count[a])[:12]])

label = {a: "confirmed" for a in red}
why = {}
changed = True
while changed:
    changed = False
    for a, r in rows.items():
        if a in label:
            continue
        link = [x for x in r["paid"] if label.get(x) in ("confirmed", "linked")] or \
               [x for x in r["fund"] if x not in hot and label.get(x) in ("confirmed", "linked")]
        if link:
            label[a], why[a] = "linked", link[0]
            changed = True
# funders/payees that are not candidates themselves can carry a link too (an emptied intermediate address)
inter = collections.defaultdict(set)
for a, r in rows.items():
    for x in list(r["fund"]) + list(r["paid"]):
        if x not in hot and x not in rows and x != DEP:
            inter[x].add(a)
changed = True
while changed:
    changed = False
    for x, members in inter.items():
        if any(label.get(m) in ("confirmed", "linked") for m in members):
            for m in members:
                if m not in label and x in rows[m]["fund"]:       # siblings split from the same source address
                    label[m], why[m] = "linked", f"same source {x}"
                    changed = True
    for a, r in rows.items():
        if a in label:
            continue
        link = [x for x in r["paid"] if label.get(x) in ("confirmed", "linked")] or \
               [x for x in r["fund"] if x not in hot and label.get(x) in ("confirmed", "linked")]
        if link:
            label[a], why[a] = "linked", link[0]
            changed = True
for a, r in rows.items():
    if a in label:
        continue
    ext = [x for x in r["paid"] if x not in rows]
    if ext:
        label[a], why[a] = "other", ext[0]
    elif r["fund"] and set(r["fund"]) <= hot:
        label[a] = "untouched"
    else:
        label[a] = "unlinked"

bal = lambda a: rows[a]["bal"] / 1e8 if a in rows and rows[a].get("complete", True) else rich.get(a, 0.0)
tab = collections.defaultdict(lambda: [0, 0.0, 0])
for a in rows:
    t = tab[label[a]]
    t[0] += 1
    t[1] += bal(a)
    t[2] += bal(a) > 1
for k, (n, b, h) in sorted(tab.items()):
    print(f"{k:10} {n:5d} addresses, {h:5d} holding, {b:12,.1f} BTC")
others = collections.Counter(why[a] for a in rows if label[a] == "other")
print("other destinations:", others.most_common(8))
un = [a for a in rows if label[a] == "unlinked"]
print("unlinked funders sample:", [(a[:12], {k[:12]: round(v / 1e8) for k, v in rows[a]["fund"].items()}) for a in un[:6]])
yr = collections.Counter(datetime.datetime.fromtimestamp(rows[a]["first"], datetime.timezone.utc).strftime("%Y-%m") for a in rows if label[a] in ("untouched", "unlinked") and rows[a]["first"])
print("untouched/unlinked first seen by month:", sorted(yr.items()))
json.dump({"label": {a: label[a] for a in rows}, "why": why, "balance": {a: bal(a) for a in rows}, "hot": sorted(hot)}, open(f"{T}/ibit_result.json", "w"))
