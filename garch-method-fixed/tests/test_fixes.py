"""
Regression tests for the fixes in this fork (see README "Fork changes").

Run with the dependencies installed, e.g. from the repo root:
    ~/.cache/garch-method/venv/bin/python -m unittest discover -s tests -v
"""
import re
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import garch_forecast as gf  # noqa: E402
import vol_target as vt  # noqa: E402
from arch import arch_model  # noqa: E402

ENTRY_SCRIPTS = ("garch_forecast.py", "vol_target.py", "compare.py")


def synthetic_prices(n=720, start="2023-01-01", seed=3, freq="D"):
    """Price path driven by a GARCH(1,1)-t process, so fits are well behaved."""
    rng = np.random.default_rng(seed)
    omega, alpha, beta, nu = 0.05, 0.08, 0.90, 5.0
    s2 = omega / (1 - alpha - beta)
    r = np.empty(n)
    for i in range(n):
        r[i] = np.sqrt(s2) * rng.standard_t(nu) * np.sqrt((nu - 2) / nu)
        s2 = omega + alpha * r[i] ** 2 + beta * s2
    dates = pd.date_range(start, periods=n + 1, freq=freq)
    close = 100 * np.cumprod(np.concatenate([[1.0], 1 + r / 100]))
    return pd.DataFrame({"date": dates, "close": close})


def after_close(px, days=1, hours=1):
    """A 'now' at which the last bar of a 24/7 series is complete and current."""
    return px["date"].iloc[-1] + pd.Timedelta(days=days, hours=hours)


def arch_forecast(r, t, fit_end):
    """arch's own variance forecast for day t+1, with params fitted on r[:fit_end]."""
    fit = arch_model(r[:fit_end], vol="GARCH", p=1, q=1, mean="Constant", dist="t").fit(
        disp="off", show_warning=False)
    fixed = arch_model(r[: t + 1], vol="GARCH", p=1, q=1, mean="Constant", dist="t").fix(fit.params)
    return float(fixed.forecast(horizon=1).variance.to_numpy()[-1, 0])


class RefitInitialisation(unittest.TestCase):
    """v1.0.0 restarted each refit from day t-1's variance, dropping day t-1's shock."""

    @classmethod
    def setUpClass(cls):
        cls.px = synthetic_prices()
        cls.wf = gf.walkforward_garch(cls.px, min_train=500, refit_every=21)
        p = cls.px["close"].to_numpy(float)
        cls.r = 100 * np.diff(p) / p[:-1]

    def test_refit_days_match_arch(self):
        for t in (500, 521, 542):
            want = arch_forecast(self.r, t, fit_end=t)
            got = self.wf["fcast_vol"].iloc[t] ** 2
            self.assertAlmostEqual(got / want, 1.0, places=6, msg=f"refit day t={t}")

    def test_days_between_refits_match_arch(self):
        for t, fit_end in ((510, 500), (530, 521)):
            want = arch_forecast(self.r, t, fit_end=fit_end)
            got = self.wf["fcast_vol"].iloc[t] ** 2
            self.assertAlmostEqual(got / want, 1.0, places=6, msg=f"t={t}")


class DataChecks(unittest.TestCase):
    def test_drops_still_open_crypto_bar(self):
        px = synthetic_prices(n=30)
        now = px["date"].iloc[-1] + pd.Timedelta(hours=7)  # last bar's UTC day is still running
        out, q = gf.validate_prices(px, 365, now=now)
        self.assertEqual(out["date"].iloc[-1], px["date"].iloc[-2])
        self.assertTrue(any("still-open" in m for m in q["notes"]), q)
        self.assertTrue(q["ok"], q)

    def test_keep_partial_keeps_it(self):
        px = synthetic_prices(n=30)
        now = px["date"].iloc[-1] + pd.Timedelta(hours=7)
        out, q = gf.validate_prices(px, 365, keep_partial=True, now=now)
        self.assertEqual(out["date"].iloc[-1], px["date"].iloc[-1])

    def test_missing_day_flagged_and_spanned(self):
        px = synthetic_prices(n=40)
        gap_day = px["date"].iloc[-5]
        px = px[px["date"] != gap_day].reset_index(drop=True)
        out, q = gf.validate_prices(px, 365, now=after_close(px))
        self.assertFalse(q["ok"], q)
        self.assertIn(gap_day.date().isoformat(), q["missing_days_recent"])
        row = out.loc[out["date"] == gap_day + pd.Timedelta(days=1)]
        self.assertEqual(int(row["span"].iloc[0]), 2)
        self.assertEqual(int(out["span"].sum()), len(out) + 1)

    def test_old_gap_is_a_note_not_an_issue(self):
        px = synthetic_prices(n=120)
        px = px.drop(index=10).reset_index(drop=True)
        out, q = gf.validate_prices(px, 365, now=after_close(px))
        self.assertTrue(q["ok"], q)
        self.assertTrue(any("missing calendar day" in m for m in q["notes"]), q)

    def test_stale_series_not_ok(self):
        px = synthetic_prices(n=30)
        out, q = gf.validate_prices(px, 365, now=after_close(px, days=3))
        self.assertEqual(q["bars_behind"], 2)
        self.assertFalse(q["ok"], q)

    def test_equity_weekends_are_not_gaps(self):
        px = synthetic_prices(n=60, freq="B", start="2024-01-01")
        now = (px["date"].iloc[-1] + pd.Timedelta(hours=23)).tz_localize("America/New_York")
        out, q = gf.validate_prices(px, 252, now=now)
        self.assertTrue(q["ok"], q)
        self.assertTrue((out["span"] == 1).all())

    def test_equity_bar_open_before_close(self):
        px = synthetic_prices(n=60, freq="B", start="2024-01-01")
        now = (px["date"].iloc[-1] + pd.Timedelta(hours=11)).tz_localize("America/New_York")
        out, q = gf.validate_prices(px, 252, now=now)
        self.assertEqual(out["date"].iloc[-1], px["date"].iloc[-2])

    def test_duplicates_and_bad_rows(self):
        px = synthetic_prices(n=30)
        dup = px.iloc[[5]].assign(close=px["close"].iloc[5] * 1.01)
        bad = pd.DataFrame({"date": ["not a date", px["date"].iloc[3]], "close": [1.0, -5.0]})
        messy = pd.concat([px, dup, bad], ignore_index=True)
        out, q = gf.validate_prices(messy, 365, now=after_close(px))
        self.assertEqual(len(out), len(px))
        self.assertAlmostEqual(out.loc[5, "close"], px["close"].iloc[5] * 1.01)
        self.assertTrue(q["ok"], q)

    def test_gap_return_is_scaled_for_garch(self):
        px = synthetic_prices()
        scaled = px.assign(span=1)
        scaled.loc[650, "span"] = 4
        a = gf.walkforward_garch(px)
        b = gf.walkforward_garch(scaled)
        np.testing.assert_allclose(a["fcast_vol"].iloc[500:648], b["fcast_vol"].iloc[500:648])
        self.assertNotAlmostEqual(a["fcast_vol"].iloc[649], b["fcast_vol"].iloc[649], places=6)
        np.testing.assert_allclose(a["ret"], b["ret"])  # true returns are untouched


class RegimeAndTarget(unittest.TestCase):
    def test_percentile_ignores_warmup_nans(self):
        w = np.array([np.nan] * 200 + list(range(100)) + [50.5])
        self.assertAlmostEqual(gf._pctile(w), 51.0)
        self.assertTrue(np.isnan(gf._pctile(np.array([np.nan] * 300 + [1.0, 2.0]))))

    def test_auto_target_uses_only_the_past(self):
        s = pd.Series([10.0, 20.0, 30.0, 1000.0])
        self.assertEqual(list(vt.auto_target(s, lookback=3)), [10.0, 15.0, 20.0, 30.0])

    def test_parse_target(self):
        self.assertEqual(vt.parse_target("auto"), "auto")
        self.assertEqual(vt.parse_target("58"), 58.0)
        with self.assertRaises(Exception):
            vt.parse_target("-3")


class Packaging(unittest.TestCase):
    @staticmethod
    def declared(name):
        header = (ROOT / "scripts" / name).read_text().split("# ///")[1]
        specs = re.findall(r'"([^"]+)"', re.search(r"dependencies = \[(.*)\]", header).group(1))
        return {re.split(r"[<>=!~ ]", s)[0] for s in specs}

    def test_entry_scripts_declare_yfinance(self):
        for name in ENTRY_SCRIPTS:
            self.assertIn("yfinance", self.declared(name), name)

    def test_launcher_installs_every_declared_dependency(self):
        launcher = (ROOT / "scripts" / "garch").read_text()
        for name in ENTRY_SCRIPTS:
            for dep in self.declared(name):
                self.assertIn(f'"{dep}', launcher, f"{dep} ({name}) missing from scripts/garch")


if __name__ == "__main__":
    unittest.main()
