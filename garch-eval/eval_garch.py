#!/usr/bin/env python3
"""
Independent audit of the garch-method plugin
  (github.com/milesdeutscher/garchmethod @ e1e93d9, v1.0.0)

usage: python -I eval_garch.py <plugin_scripts_dir> <yf_csv> <coinmetrics_csv> <outdir>
"""
import json
import sys
import time
import urllib.request
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
PLUGIN, YF_CSV, CM_CSV, OUTDIR = (Path(a).resolve() for a in sys.argv[1:5])
sys.path.insert(0, str(PLUGIN))
import garch_forecast as gf  # noqa: E402  plugin code (reviewed line by line)
import compare as cmp  # noqa: E402  plugin code (reviewed)
from arch import arch_model  # noqa: E402
import statsmodels.api as sm  # noqa: E402

PPY = 365
MIN_TRAIN, REFIT = gf.MIN_TRAIN, gf.REFIT_EVERY
TODAY = pd.Timestamp("2026-11-13")
UA = {"User-Agent": "Mozilla/5.0 (research script)"}
R: dict = {}
rng = np.random.default_rng(7)


def hdr(s):
    print(f"\n{'=' * 96}\n{s}\n{'=' * 96}", flush=True)


def http_json(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def ann(v):  # daily %^2 variance -> annualised % vol
    return np.sqrt(np.asarray(v, dtype=float) * PPY)


def size(vol_ann, target, lo=0.25, hi=2.0):
    return np.clip(target / np.asarray(vol_ann, dtype=float), lo, hi)


def perf(net, dates):
    r = pd.Series(np.asarray(net, float), index=pd.DatetimeIndex(dates))
    eq = (1 + r).cumprod()
    yrs = len(r) / PPY
    vol = r.std() * np.sqrt(PPY)
    return dict(
        cagr=100 * (eq.iloc[-1] ** (1 / yrs) - 1),
        vol=100 * vol,
        sharpe=r.mean() * PPY / vol,
        maxdd=100 * (eq / eq.cummax() - 1).min(),
        worst_m=100 * r.resample("ME").apply(lambda x: (1 + x).prod() - 1).min(),
        final=eq.iloc[-1],
    )


def bt(sig, mult, rnext, cost_bps=0.0, fin=0.0):
    """position at close t = sig_t * mult_t, earns r_{t+1}; cost on turnover; financing on >1x."""
    pos = np.asarray(sig, float) * np.asarray(mult, float)
    turn = np.abs(np.diff(pos, prepend=0.0))
    net = pos * rnext / 100 - turn * cost_bps / 1e4 - np.clip(pos - 1, 0, None) * fin / PPY
    return net, turn.sum() / (len(pos) / PPY)


def line(name, p, extra=""):
    return (f"  {name:<40}CAGR {p['cagr']:7.1f}%  vol {p['vol']:5.1f}%  Sharpe {p['sharpe']:5.2f}  "
            f"maxDD {p['maxdd']:6.1f}%  worstM {p['worst_m']:6.1f}%{extra}")


def sharpe(x):
    return x.mean() / x.std() * np.sqrt(PPY)


def sb_idx(n, mean_block, g):
    out, tot = [], 0
    while tot < n:
        L = int(g.geometric(1 / mean_block))
        out.append((g.integers(n) + np.arange(L)) % n)
        tot += L
    return np.concatenate(out)[:n]


def boot_sharpe_diff(a, b, reps=2000, block=20):
    """stationary block bootstrap of Sharpe(a) - Sharpe(b), paired."""
    d = np.empty(reps)
    for i in range(reps):
        ix = sb_idx(len(a), block, rng)
        d[i] = sharpe(a[ix]) - sharpe(b[ix])
    return np.percentile(d, [2.5, 50, 97.5]), (d <= 0).mean()


def dm(la, lb, L=10):
    d = la - lb
    r = sm.OLS(d, np.ones_like(d)).fit(cov_type="HAC", cov_kwds={"maxlags": L})
    return float(r.params[0]), float(r.tvalues[0])


def payload_from(wfr, target):
    last = wfr.dropna(subset=["fcast_vol"]).iloc[-1]
    return dict(as_of=str(last.date.date()), vol_ann=round(float(last.fcast_vol_ann), 1),
                pctile=None if pd.isna(last.vol_pctile) else round(float(last.vol_pctile), 1),
                regime=str(last.regime), mult=round(float(size(last.fcast_vol_ann, target)), 2),
                last_ret=round(float(last.ret), 2))


# ======================================================================== 1. data
hdr("1. DATA HYGIENE: what the plugin's --ticker path feeds the model")
yf = pd.read_csv(YF_CSV, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
cal = pd.date_range(yf.date.min(), yf.date.max(), freq="D")
missing = cal.difference(pd.DatetimeIndex(yf.date))
print(f"yfinance BTC-USD: {len(yf)} rows {yf.date.min().date()} -> {yf.date.max().date()}")
print(f"missing calendar days: {len(missing)}  latest: {[d.date().isoformat() for d in missing[-5:]]}")
print(f"duplicate dates: {int(yf.date.duplicated().sum())}")
print(f"last bar {yf.date.iloc[-1].date()} is today's still-open UTC day -> partial bar: {yf.date.iloc[-1] >= TODAY}")
R["data"] = dict(rows=len(yf), missing_days=[d.date().isoformat() for d in missing],
                 partial_last_bar=bool(yf.date.iloc[-1] >= TODAY))

cm = pd.read_csv(CM_CSV, parse_dates=["date"]).rename(columns={"price": "close"})


def daily_ret(df):
    s = df.set_index("date")["close"].asfreq("D")
    return 100 * (s / s.shift(1) - 1)


both = pd.concat([daily_ret(yf).rename("yf"), daily_ret(cm).rename("cm")], axis=1).dropna()
dd_ = (both.yf - both.cm).abs()
print(f"yfinance vs CoinMetrics daily returns (n={len(both)}): corr {both.corr().iloc[0, 1]:.4f}, "
      f"median |diff| {dd_.median():.3f}pp, days with |diff| > 2pp: {(dd_ > 2).sum()}")

clean = yf[yf.date < TODAY].copy()
recent_gaps = missing[missing > clean.date.max() - pd.Timedelta(days=30)]
if len(recent_gaps):
    clean = clean[clean.date < recent_gaps.min()]
clean = clean.reset_index(drop=True)
print(f"evaluation series: {clean.date.min().date()} -> {clean.date.max().date()} ({len(clean)} rows; "
      f"partial bar and post-gap rows dropped)")

# ======================================================================== 2. replication
hdr("2. REPLICATION + REFIT-INITIALISATION CHECK")
t0 = time.time()
wf = gf.walkforward_garch(clean, periods_per_year=PPY)
print(f"plugin walkforward_garch(): {time.time() - t0:.0f}s; first forecast made {wf.date[MIN_TRAIN].date()}")
px = clean.close.to_numpy(float)
rets = 100 * np.diff(px) / px[:-1]
n = len(rets)
fv_p = np.full(n, np.nan)
fv_f = np.full(n, np.nan)
par = np.full((n, 5), np.nan)  # mu, omega, alpha, beta, nu in force when forecast t is made
refits = []
for t in range(MIN_TRAIN, n):
    if (t - MIN_TRAIN) % REFIT == 0:
        res = arch_model(rets[:t], vol="GARCH", p=1, q=1, mean="Constant", dist="t").fit(
            disp="off", show_warning=False)
        p = res.params
        cur = np.array([p["mu"], p["omega"], p["alpha[1]"], p["beta[1]"], p["nu"]])
        s_p = float(res.conditional_volatility[-1] ** 2)            # plugin: sigma^2 of day t-1
        s_f = float(res.forecast(horizon=1).variance.values[-1, 0])  # correct: sigma^2 of day t
        refits.append((t, *cur, s_p, s_f))
    mu, om, al, be, nu = cur
    e2 = (rets[t] - mu) ** 2
    s_p = om + al * e2 + be * s_p
    s_f = om + al * e2 + be * s_f
    fv_p[t], fv_f[t] = s_p, s_f
    par[t] = cur
same = np.allclose(np.sqrt(fv_p[MIN_TRAIN:]), wf.fcast_vol.to_numpy()[MIN_TRAIN:], rtol=1e-9, atol=1e-12)
print(f"my re-implementation reproduces the plugin's forecasts exactly: {same}")

# gold standard: arch's own filter with the same fixed params on data through t
refit_ts = np.arange(MIN_TRAIN, n, REFIT)
check_ts = list(rng.choice(refit_ts[1:], 5, replace=False)) + list(rng.choice(np.arange(MIN_TRAIN, n), 5, replace=False))
errs_f, errs_p = [], []
for t in map(int, check_ts):
    g = arch_model(rets[: t + 1], vol="GARCH", p=1, q=1, mean="Constant", dist="t").fix(list(par[t]))
    gold = float(g.forecast(horizon=1).variance.values[-1, 0])
    errs_f.append(abs(np.sqrt(fv_f[t] / gold) - 1) * 100)
    errs_p.append(abs(np.sqrt(fv_p[t] / gold) - 1) * 100)
print(f"vs arch's own fixed-param forecast at 10 dates (5 refit days): corrected max err {max(errs_f):.4f}%  "
      f"| plugin max err {max(errs_p):.2f}%")

v = np.arange(MIN_TRAIN, n)
rel = (np.sqrt(fv_p[v] / fv_f[v]) - 1) * 100
isr = (v - MIN_TRAIN) % REFIT == 0
ar = np.abs(rel)
print("plugin vs corrected 1-day vol forecast (% difference in vol):")
print(f"  refit days (n={isr.sum()}): median |d| {np.median(ar[isr]):.2f}%  95th pct {np.percentile(ar[isr], 95):.2f}%  "
      f"max {ar[isr].max():.1f}%")
print(f"  all days (n={len(v)}): median |d| {np.median(ar):.3f}%  days |d|>2%: {(ar > 2).sum()}  "
      f"|d|>5%: {(ar > 5).sum()}  |d|>10%: {(ar > 10).sum()}  mean signed {rel.mean():+.3f}%")
w = v[np.argmax(ar)]
print(f"  worst: forecast made {wf.date[w].date()}: plugin {ann(fv_p[w]):.1f}% vs correct {ann(fv_f[w]):.1f}% annualised")
mp58, mf58 = size(ann(fv_p[v]), 58), size(ann(fv_f[v]), 58)
print(f"  size multiplier @58% target: mean |diff| {np.mean(np.abs(mp58 - mf58)):.4f}x  max {np.max(np.abs(mp58 - mf58)):.3f}x")
R["refit_bug"] = dict(reproduces_plugin=bool(same), refit_days=int(isr.sum()),
                      refit_median_abs_pct=float(np.median(ar[isr])), refit_max_abs_pct=float(ar[isr].max()),
                      days_gt5=int((ar > 5).sum()), max_mult_diff=float(np.max(np.abs(mp58 - mf58))))

# ======================================================================== 3. params
hdr("3. FITTED GARCH(1,1)-t PARAMETERS ACROSS REFITS")
rf = pd.DataFrame(refits, columns=["t", "mu", "omega", "alpha", "beta", "nu", "s_p", "s_f"])
rf["date"] = wf.date.to_numpy()[rf.t.to_numpy()]
rf["persist"] = rf.alpha + rf.beta
rf["half_life_d"] = np.log(0.5) / np.log(rf.persist)
rf["lr_vol_ann"] = ann(rf.omega / (1 - rf.persist))
for lab, r_ in (("first", rf.iloc[0]), ("middle", rf.iloc[len(rf) // 2]), ("latest", rf.iloc[-1])):
    print(f"  {lab:<7}{pd.Timestamp(r_.date).date()}  alpha {r_.alpha:.3f}  beta {r_.beta:.3f}  "
          f"alpha+beta {r_.persist:.4f}  half-life {r_.half_life_d:5.1f}d  nu {r_.nu:.2f}  "
          f"long-run vol {r_.lr_vol_ann:5.1f}%")
print(f"  ranges: alpha+beta {rf.persist.min():.3f}-{rf.persist.max():.3f}, nu {rf.nu.min():.2f}-{rf.nu.max():.2f}, "
      f"long-run vol {rf.lr_vol_ann.min():.0f}-{rf.lr_vol_ann.max():.0f}%")
R["params_latest"] = rf.iloc[-1][["alpha", "beta", "persist", "half_life_d", "nu", "lr_vol_ann"]].astype(float).round(4).to_dict()

# ======================================================================== 4. 1-day accuracy
hdr("4. ONE-DAY-AHEAD FORECAST ACCURACY vs TRIVIAL BENCHMARKS (out of sample, same days)")
ew = np.full(n, np.nan)
s = np.var(rets[:30])
for t in range(n):
    s = 0.94 * s + 0.06 * rets[t] ** 2
    ew[t] = s
rv30 = pd.Series(rets ** 2).rolling(30).mean().to_numpy()
expd = pd.Series(rets ** 2).expanding(30).mean().to_numpy()
idx = np.arange(MIN_TRAIN, n - 1)
y = rets[idx + 1] ** 2
models = {"GARCH(1,1)-t plugin": fv_p, "GARCH(1,1)-t corrected": fv_f,
          "EWMA 0.94 (RiskMetrics, no fitting)": ew, "30-day realised": rv30, "expanding mean (no model)": expd}
ql = {k: np.log(m[idx]) + y / m[idx] for k, m in models.items()}
base = ql["EWMA 0.94 (RiskMetrics, no fitting)"]
print(f"  {'model':<38}{'QLIKE':>9}{'vs EWMA (DM t)':>18}{'MZ R2 on r^2':>15}")
R["one_day"] = {}
for k, m in models.items():
    mean_d, tstat = dm(ql[k], base)
    X = sm.add_constant(m[idx])
    r2 = sm.OLS(y, X).fit().rsquared
    print(f"  {k:<38}{ql[k].mean():9.4f}{mean_d:+10.4f} ({tstat:+5.1f}){r2:15.3f}")
    R["one_day"][k] = dict(qlike=float(ql[k].mean()), dm_vs_ewma_t=tstat, mz_r2=float(r2))
print("  (lower QLIKE = better; DM t < -2 means significantly better than EWMA)")

# ======================================================================== 5. horizons + DVOL
hdr("5. OPTION-TENOR HORIZONS: predicting the NEXT 7 / 30 DAYS' realised vol, incl. Deribit DVOL")
csum = np.concatenate([[0.0], np.cumsum(rets ** 2)])


def fwd_rv(h):
    out = np.full(n, np.nan)
    out[: n - h] = (csum[h + 1: n + 1] - csum[1: n - h + 1]) / h
    return ann(out)


def garch_h(fv, h):
    om_, al_, be_ = par[:, 1], par[:, 2], par[:, 3]
    phi = al_ + be_
    sbar = om_ / (1 - phi)
    avg = np.where(phi < 1, sbar + (fv - sbar) * (1 - phi ** h) / (h * (1 - phi)), fv)
    return ann(avg)


try:
    rows, end = [], int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    start = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    for _ in range(20):
        res_ = http_json("https://www.deribit.com/api/v2/public/get_volatility_index_data"
                         f"?currency=BTC&start_timestamp={start}&end_timestamp={end}&resolution=1D")["result"]
        rows += res_["data"]
        if not res_.get("continuation"):
            break
        end = res_["continuation"]
    dv = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c"]).drop_duplicates("ts")
    dv["date"] = pd.to_datetime(dv.ts, unit="ms").dt.normalize()
    dv.sort_values("date")[["date", "c"]].to_csv(OUTDIR / "dvol_btc_daily.csv", index=False)
    dvol = wf.date.map(dv.set_index("date")["c"]).to_numpy(float)
    print(f"DVOL fetched: {dv.date.min().date()} -> {dv.date.max().date()} ({len(dv)} days)")
except Exception as e:  # noqa: BLE001
    dvol = np.full(n, np.nan)
    print(f"DVOL fetch failed: {e}")

R["horizons"] = {}
for h in (7, 30):
    real = fwd_rv(h)
    preds = {"plugin 1-day forecast, annualised": ann(fv_p), f"GARCH {h}d term forecast (corrected)": garch_h(fv_f, h),
             "EWMA 0.94": ann(ew), "trailing 30d realised": ann(rv30), "Deribit DVOL (30d implied)": dvol}
    for scope, mask_extra in (("full sample", np.isfinite(real)), ("DVOL era only", np.isfinite(real) & np.isfinite(dvol))):
        m = np.zeros(n, bool)
        m[MIN_TRAIN:] = True
        m &= mask_extra
        if m.sum() < 100:
            continue
        print(f"\n  h={h}d, {scope} (n={m.sum()}): predicting realised vol over the next {h} days (annualised %)")
        print(f"    {'predictor':<40}{'bias':>8}{'RMSE':>8}{'MZ slope':>10}{'MZ R2':>8}")
        for k, pr in preds.items():
            mm = m & np.isfinite(pr)
            if mm.sum() < m.sum() * 0.99:
                continue
            pp, rr = pr[mm], real[mm]
            ols = sm.OLS(rr, sm.add_constant(pp)).fit()
            print(f"    {k:<40}{np.mean(pp - rr):+8.1f}{np.sqrt(np.mean((pp - rr) ** 2)):8.1f}"
                  f"{ols.params[1]:10.2f}{ols.rsquared:8.3f}")
            R["horizons"][f"{h}d|{scope}|{k}"] = dict(bias=float(np.mean(pp - rr)),
                                                       rmse=float(np.sqrt(np.mean((pp - rr) ** 2))),
                                                       r2=float(ols.rsquared))
    if h == 30 and np.isfinite(dvol).sum() > 300:
        g30 = garch_h(fv_f, 30)
        m = np.isfinite(real) & np.isfinite(dvol) & np.isfinite(g30)
        m[:MIN_TRAIN] = False
        X = sm.add_constant(np.column_stack([dvol[m], g30[m]]))
        enc = sm.OLS(real[m], X).fit(cov_type="HAC", cov_kwds={"maxlags": 30})
        print(f"\n  encompassing (HAC-30): realised30 = {enc.params[0]:.1f} + {enc.params[1]:.2f}*DVOL "
              f"(t={enc.tvalues[1]:.1f}) + {enc.params[2]:.2f}*GARCH30 (t={enc.tvalues[2]:.1f}); R2 {enc.rsquared:.3f}")
        vrp_g = dvol[m] - g30[m]
        vrp_r = dvol[m] - real[m]
        print(f"  DVOL minus GARCH30 forecast: mean {vrp_g.mean():+.1f} vol pts, median {np.median(vrp_g):+.1f}, "
              f"positive {100 * (vrp_g > 0).mean():.0f}% of days")
        print(f"  DVOL minus realised-next-30d: mean {vrp_r.mean():+.1f} vol pts, positive {100 * (vrp_r > 0).mean():.0f}% of days")
        R["dvol"] = dict(enc_b_dvol=float(enc.params[1]), enc_t_dvol=float(enc.tvalues[1]),
                         enc_b_garch=float(enc.params[2]), enc_t_garch=float(enc.tvalues[2]),
                         vrp_vs_garch_mean=float(vrp_g.mean()), vrp_vs_real_mean=float(vrp_r.mean()))

# ======================================================================== 6. backtests
hdr("6. THE 'HONEST TEST' (compare.py) REPLICATED, THEN STRESSED WITH COSTS / ESTIMATORS / TARGETS")
cmp.walkforward_garch = lambda prices, periods_per_year=PPY: wf  # reuse the cached walk-forward (deterministic)
stats58, _ = cmp.run_comparison(clean, signals=None, target_vol=58, periods_per_year=PPY)
print("plugin compare.py, verbatim (EMA 9/21 long/flat, target 58%, NO costs):")
for k_, s_ in stats58.items():
    print(f"  {k_:<14}{s_}")
R["compare_py_58"] = stats58

ema = cmp.ema_crossover_signals(wf.close).to_numpy()
hold = np.ones(n)
rnext = np.append(rets[1:], np.nan)
V = np.arange(MIN_TRAIN, n - 1)
dts = wf.date.to_numpy()[V]
est = {"GARCH (plugin)": ann(fv_p), "GARCH (corrected)": ann(fv_f), "EWMA 0.94": ann(ew), "30d realised": ann(rv30)}
net_cache = {}
R["bt"] = {}
for sname, sig in (("EMA 9/21 long/flat", ema), ("Buy & hold", hold)):
    print(f"\n  --- {sname} ---")
    for cost, fin in ((0, 0.0), (5, 0.10), (10, 0.10)):
        tag = f"{cost}bps" + (" +10%/yr financing >1x" if fin else " (as plugin)")
        print(f"  [{tag}]")
        net, to = bt(sig[V], np.ones(len(V)), rnext[V], cost, fin)
        pf = perf(net, dts)
        print(line("fixed 1x", pf, f"  turnover {to:5.1f}x/yr"))
        net_cache[(sname, cost, "fixed")] = net
        R["bt"][f"{sname}|{cost}|fixed"] = pf
        for ename, vol_ in est.items():
            net, to = bt(sig[V], size(vol_[V], 58), rnext[V], cost, fin)
            p_ = perf(net, dts)
            print(line(f"vol-targeted 58% via {ename}", p_, f"  turnover {to:5.1f}x/yr"))
            net_cache[(sname, cost, ename)] = net
            R["bt"][f"{sname}|{cost}|{ename}"] = p_

print("\n  risk-matched drawdown check (fixed scaled to the vol-targeted book's realised vol; 5bps):")
for sname in ("EMA 9/21 long/flat", "Buy & hold"):
    a, b = net_cache[(sname, 5, "GARCH (plugin)")], net_cache[(sname, 5, "fixed")]
    k = a.std() / b.std()
    pa, pb = perf(a, dts), perf(b * k, dts)
    print(f"  {sname:<20} vol-targeted: maxDD {pa['maxdd']:6.1f}%  worstM {pa['worst_m']:6.1f}%  CAGR {pa['cagr']:6.1f}%"
          f"   | fixed x{k:.2f}: maxDD {pb['maxdd']:6.1f}%  worstM {pb['worst_m']:6.1f}%  CAGR {pb['cagr']:6.1f}%")

print("\n  target-vol sensitivity (GARCH plugin, 5bps + financing): Sharpe | % days pinned at 0.25 floor | at 2.0 cap")
R["target_sens"] = {}
for sname, sig in (("EMA 9/21 long/flat", ema), ("Buy & hold", hold)):
    base_sh = perf(net_cache[(sname, 5, "fixed")], dts)["sharpe"]
    row = []
    for T in (15, 30, 40, 50, 58, 70, 85, 100):
        mlt = size(ann(fv_p[V]), T)
        net, _ = bt(sig[V], mlt, rnext[V], 5, 0.10)
        sh = perf(net, dts)["sharpe"]
        row.append(f"{T}%:{sh:.2f}|{100 * (mlt <= 0.25).mean():.0f}|{100 * (mlt >= 2).mean():.0f}")
        R["target_sens"][f"{sname}|{T}"] = sh
    print(f"  {sname:<20} fixed {base_sh:.2f} ::  " + "  ".join(row))

print("\n  sub-period Sharpe, fixed vs GARCH-vol-targeted @58% (5bps + financing):")
periods = [("2016-01", "2018-12"), ("2019-01", "2021-12"), ("2022-01", "2024-12"), ("2025-01", "2026-11")]
R["subperiods"] = {}
for sname in ("EMA 9/21 long/flat", "Buy & hold"):
    out = []
    for a_, b_ in periods:
        mk = (dts >= np.datetime64(a_ + "-01")) & (dts <= np.datetime64(pd.Timestamp(b_ + "-01") + pd.offsets.MonthEnd(0)))
        fx = sharpe(net_cache[(sname, 5, "fixed")][mk])
        vt = sharpe(net_cache[(sname, 5, "GARCH (plugin)")][mk])
        out.append(f"{a_[:4]}-{b_[:4]}: {fx:5.2f} -> {vt:5.2f}")
        R["subperiods"][f"{sname}|{a_[:4]}-{b_[:4]}"] = (float(fx), float(vt))
    print(f"  {sname:<20} " + "   ".join(out))

print("\n  is the Sharpe improvement real? paired stationary block bootstrap (20d blocks, 2000 reps, 5bps+fin):")
R["boot"] = {}
for sname in ("EMA 9/21 long/flat", "Buy & hold"):
    for ename in ("GARCH (plugin)", "EWMA 0.94"):
        ci, p0 = boot_sharpe_diff(net_cache[(sname, 5, ename)], net_cache[(sname, 5, "fixed")])
        print(f"  {sname:<20} {ename:<16} Sharpe diff median {ci[1]:+.2f}  95% CI [{ci[0]:+.2f}, {ci[2]:+.2f}]  "
              f"P(diff<=0) {p0:.3f}")
        R["boot"][f"{sname}|{ename}"] = dict(ci=[float(x) for x in ci], p_le0=float(p0))
ci, p0 = boot_sharpe_diff(net_cache[("Buy & hold", 5, "GARCH (plugin)")], net_cache[("Buy & hold", 5, "EWMA 0.94")])
print(f"  GARCH-VT minus EWMA-VT (buy & hold): median {ci[1]:+.3f}  95% CI [{ci[0]:+.3f}, {ci[2]:+.3f}]")
R["boot"]["garch_minus_ewma_bh"] = dict(ci=[float(x) for x in ci], p_le0=float(p0))

# ======================================================================== 7. why it works (or not) on BTC
hdr("7. WHY: next-day BTC returns by forecast-vol quintile (buy & hold, descriptive)")
fva = ann(fv_p[V])
q = pd.qcut(fva, 5, labels=False)
print(f"  {'quintile':<10}{'fcast vol range':>18}{'ann mean':>10}{'ann vol':>9}{'Sharpe':>8}")
R["quintiles"] = []
for k in range(5):
    mk = q == k
    rr = rnext[V][mk] / 100
    am, av = rr.mean() * PPY * 100, rr.std() * np.sqrt(PPY) * 100
    print(f"  Q{k + 1:<9}{fva[mk].min():8.0f}-{fva[mk].max():<8.0f}{am:10.0f}%{av:8.0f}%{am / av:8.2f}")
    R["quintiles"].append(dict(lo=float(fva[mk].min()), hi=float(fva[mk].max()), mean=float(am), vol=float(av)))

# ======================================================================== 8. labels / defaults
hdr("8. REGIME LABEL + DEFAULT TARGET")
reg = wf.regime.astype(str).to_numpy()[V]
first_year = dts < dts[0] + np.timedelta64(365, "D")
print("  share of days:", {k: f"{100 * (reg == k).mean():.0f}%" for k in ("calm", "normal", "storm", "nan")})
print("  first 365 days of forecasts:", {k: f"{100 * (reg[first_year] == k).mean():.0f}%" for k in ("calm", "normal", "storm", "nan")})
m15 = size(ann(fv_p[V]), 15)
print(f"  default --target-vol 15 on BTC: median multiplier {np.median(m15):.2f}x, pinned at 0.25 floor "
      f"{100 * (m15 <= 0.25).mean():.0f}% of days")
print(f"  JSON example in SKILL.md: forecast 41.2% -> 0.6x implies target {41.2 * 0.6:.1f}%, not the 15% default "
      f"(15/41.2 = {15 / 41.2:.2f}x)")

# ======================================================================== 9. live
hdr("9. WHAT THE PLUGIN SAYS RIGHT NOW, AND HOW MUCH THAT DEPENDS ON THE DATA FEED")
live = {}
live["yfinance raw (exactly what --ticker feeds it)"] = payload_from(gf.walkforward_garch(yf, PPY), 58)
live["yfinance, partial bar + gap removed"] = payload_from(wf, 58)
try:
    d = http_json("https://www.okx.com/api/v5/market/index-candles?instId=BTC-USD&bar=1Dutc&limit=100")["data"]
    ok = pd.DataFrame(d, columns=["ts", "o", "h", "l", "c", "confirm"])
    ok["date"] = pd.to_datetime(ok.ts.astype("int64"), unit="ms").dt.normalize()
    ok["close"] = ok.c.astype(float)
    ok = ok[ok.confirm == "1"].sort_values("date")
    spliced = pd.concat([yf[yf.date < ok.date.min()][["date", "close"]], ok[["date", "close"]]]).reset_index(drop=True)
    gaps = pd.date_range(spliced.date.min(), spliced.date.max()).difference(pd.DatetimeIndex(spliced.date))
    live[f"OKX BTC-USD index, confirmed daily bars (gaps: {len(gaps)})"] = payload_from(gf.walkforward_garch(spliced, PPY), 58)
    print("  OKX last confirmed closes:", ", ".join(f"{d_.date()} {c_:,.0f}" for d_, c_ in zip(ok.date.tail(4), ok.close.tail(4))))
except Exception as e:  # noqa: BLE001
    print(f"  OKX fetch failed: {e}")
for k, p_ in live.items():
    print(f"  {k:<58} as_of {p_['as_of']}  vol {p_['vol_ann']:5.1f}%  pctile {p_['pctile']}  "
          f"regime {p_['regime']:<6} size@58 {p_['mult']:.2f}x  (last 'daily' return {p_['last_ret']:+.2f}%)")
R["live"] = live

(OUTDIR / "results.json").write_text(json.dumps(R, indent=2, default=str))
print(f"\nresults -> {OUTDIR / 'results.json'}")
