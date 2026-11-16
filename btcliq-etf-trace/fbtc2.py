"""Grow the FBTC (Fidelity Digital Assets) wallet set - batched version of fbtc.py, same rules, same state file.

  * every multisig-style input (P2SH / P2WSH) spent together with a known address is the same wallet;
  * an output of such a spend is the wallet's too if it is a quiet P2SH / P2WSH address
    (cold storage is single-use; market makers' deposit addresses are busy and are left out);
  * an output whose amount is an exact multiple of FBTC's share basket and that was emptied without ever being
    co-spent with the wallet is treated as a redemption payment, not cold storage.
Usage: fbtc2.py [max_rounds]"""
import json, os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bci

STATE = "/home/miller/.claude/jobs/d18f685a/tmp/fbtc_state.json"
BUSY = 60
R0, T0 = 326.0595 / 375000, datetime.datetime(2026, 11, 13, tzinfo=datetime.timezone.utc).timestamp()


def kind(a):
    if not a:
        return "?"
    if a.startswith("bc1p"):
        return "tr"
    if a.startswith("bc1q"):
        return "wsh" if len(a) > 50 else "wpkh"
    return "sh" if a.startswith("3") else "pkh"


def basket_multiple(btc, ts, tol=6e-5):
    per = R0 * (1 + 0.0025 * (T0 - ts) / 31557600) * 25000
    n = round(btc / per)
    return n >= 1 and abs(btc / (n * per) - 1) < tol


st = json.load(open(STATE))
members, queue, external, pending = st["members"], st["queue"], st["external"], st["pending"]
rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 50
for rnd in range(rounds):
    todo = [a for a in dict.fromkeys(queue) if (a in members and not members[a].get("done")) or a in pending][:600]
    if not todo:
        break
    del queue[:]
    queue.extend(a for a in st.get("_later", []))
    info = bci.balances(todo)
    n_tx = {a: info[a]["n_tx"] for a in todo if a in info}
    for a in [a for a in todo if a in pending and n_tx.get(a, 0) > BUSY]:
        external[a] = {"why": "busy address", "txs": n_tx[a]}
        pending.pop(a)
    live = [a for a in todo if a in members or a in pending]
    hist, _ = bci.histories(live, n_tx=n_tx, cap=1500)
    known = set(members)
    for a in live:
        txs = hist[a]
        co = any(a in {x for x, _ in t["vin"]} and ({x for x, _ in t["vin"]} & known) - {a} for t in txs)
        bal = info[a]["final_balance"]
        if a in pending:
            p = pending.pop(a)
            if not co and p.get("basket") and bal == 0:
                external[a] = {"why": "redemption-sized payment, not co-spent", "btc": p["btc"]}
                continue
            members[a] = {"why": p["why"] + ("; later co-spent" if co else "")}
        m = members[a]
        m.update(done=True, balance=bal / 1e8, txs=n_tx.get(a, len(txs)))
        for t in txs:
            in_addrs = {x for x, _ in t["vin"]}
            if a not in in_addrs:
                continue
            ts = t.get("time") or time.time()
            for x in in_addrs:
                if x and x not in members and kind(x) in ("sh", "wsh"):
                    pending.pop(x, None)
                    external.pop(x, None)
                    members[x] = {"why": f"co-spent with {a[:10]} in {t['txid'][:10]}"}
                    queue.append(x)
            for x, v in t["vout"]:
                if not x or x in members or x in pending or x in external:
                    continue
                if kind(x) not in ("sh", "wsh"):
                    external[x] = {"why": "paid by the wallet (not multisig-style)", "btc": v / 1e8, "tx": t["txid"][:10]}
                    continue
                pending[x] = {"why": f"output of the wallet's spend {t['txid'][:10]}", "btc": v / 1e8, "basket": basket_multiple(v / 1e8, ts)}
                queue.append(x)
    # anything still waiting from before this round
    queue.extend(a for a in members if not members[a].get("done") and a not in queue)
    queue.extend(a for a in pending if a not in queue)
    tot = sum(v.get("balance", 0) for v in members.values())
    held = sum(1 for v in members.values() if v.get("balance", 0) > 0.001)
    print(f"round {rnd + 1}: members {len(members)} ({held} holding)  waiting {len(queue)}  external {len(external)}  balance {tot:,.1f} BTC  requests {bci.calls[0]}", flush=True)
    json.dump(st, open(STATE, "w"))
json.dump(st, open(STATE, "w"))
print("END")
