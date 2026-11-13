import json, sys, urllib.request, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
PLUGIN, YF = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(PLUGIN))
import garch_forecast as gf
from arch import arch_model
UA = {"User-Agent": "Mozilla/5.0 (research script)"}
get = lambda u: json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read())

yf = pd.read_csv(YF, parse_dates=["date"]); yf = yf[yf.date <= "2026-11-11"].reset_index(drop=True)
px = yf.close.to_numpy(float); r = 100*np.diff(px)/px[:-1]
print("persistence (alpha+beta) by estimation window, fit ending 2026-11-11:")
for lab, w in [("expanding since 2014 (what the plugin does)", len(r)), ("last 1500d", 1500), ("last 1000d", 1000), ("last 730d", 730)]:
    p = arch_model(r[-w:], vol="GARCH", p=1, q=1, mean="Constant", dist="t").fit(disp="off", show_warning=False).params
    ph = p["alpha[1]"]+p["beta[1]"]
    hl = np.log(.5)/np.log(ph) if ph < 1 else float("inf")
    lr = np.sqrt(p["omega"]/(1-ph)*365) if ph < 1 else float("inf")
    print(f"  {lab:<45} alpha {p['alpha[1]']:.4f} beta {p['beta[1]']:.4f} sum {ph:.6f}  half-life {hl:6.1f}d  long-run vol {lr:6.1f}%  nu {p['nu']:.2f}")

# provisional read: OKX confirmed daily index bars + today's live index price as if it were the close
d = get("https://www.okx.com/api/v5/market/index-candles?instId=BTC-USD&bar=1Dutc&limit=100")["data"]
ok = pd.DataFrame(d, columns=["ts","o","h","l","c","confirm"]); ok["date"] = pd.to_datetime(ok.ts.astype("int64"), unit="ms").dt.normalize()
ok["close"] = ok.c.astype(float); ok = ok[ok.confirm == "1"].sort_values("date")
now = float(get("https://www.okx.com/api/v5/market/index-tickers?instId=BTC-USD")["data"][0]["idxPx"])
full = pd.read_csv(YF, parse_dates=["date"])
s = pd.concat([full[full.date < ok.date.min()][["date","close"]], ok[["date","close"]],
               pd.DataFrame({"date":[pd.Timestamp("2026-11-13")], "close":[now]})]).reset_index(drop=True)
w = gf.walkforward_garch(s, 365).iloc[-1]
print(f"\nprovisional (OKX bars + 11-13 at live index {now:,.0f}, i.e. IF today closed here): vol {w.fcast_vol_ann:.1f}%  "
      f"pctile {w.vol_pctile:.1f}  regime {w.regime}  size@58 {min(max(58/w.fcast_vol_ann,.25),2):.2f}x  (today's ret {w.ret:+.2f}%)")
