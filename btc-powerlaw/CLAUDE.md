# CLAUDE.md: btc-powerlaw

This folder supports a Claude Code session that swing-trades BTC options (OKX/Deribit-style)
using Giovanni Santostasi's Bitcoin power-law model as its long-horizon framework.

## Files
- `POWER_LAW_BRIEF.md`: the full brief (theory, verified data, out-of-sample evidence,
  critiques, playbook, invalidation rules). Copied in full below.
- `powerlaw.py`: calculator, Python standard library only. Refreshes every number in the brief.
- `data/btc_daily.csv`: cached Coin Metrics daily prices (auto-updated by the calculator).

## Before any trading decision
1. Run `python3 powerlaw.py --live` (or `--live --json`) and use its numbers. Every figure
   below is a snapshot from 2026-11-10 and goes stale.
2. Follow the ten operating rules in §0.3, above all the horizon gate (rule 1), defined risk
   only (rule 6) and the three-world test (rule 5).
3. Size by the risk-neutral loss, never by the NvPL gain (§7.4–7.5).
4. Check invalidation (§8) first. If it has triggered, the bullish bias is off.

## Related project
`../btcliq` is the live chart (liquidation map, order books, OKX options chain, Telegram news).
Use it for short-term timing; the power law sets direction, strike zones and expiries (§7.6).

---

# Bitcoin Power Law (Giovanni Santostasi): Brief for a BTC Options Swing-Trading Agent

**As of:** 2026-11-10. BTC daily close $73,661 (Coin Metrics); the OKX index was $73,744 at 2026-11-11 UTC.
**Audience:** a Claude Code session that swing-trades BTC options (OKX/Deribit-style) to grow capital and is open to new strategies.
**Calculator:** `python3 /home/miller/Desktop/claude-project/btc-powerlaw/powerlaw.py --live` (stdlib only). It refreshes every number in this brief. Run it before any decision. Section 9 has the usage.
**Basis:**
- Santostasi's own 2026 papers (primary sources).
- The 2026 out-of-sample and critical literature.
- An independent replication on daily data from 2010-07-18 to 2026-11-10. It reproduces Santostasi & Perrenod's exponent: 5.693 vs. their published 5.690 on the same window.

Numbers marked "in-sample" use parameters fitted with hindsight. Numbers marked "real-time" use only data available on each date. Prefer real-time numbers when sizing risk.

---

## 0. Operating summary (read this first)

### 0.1 The model in one paragraph
- **The fit:** Bitcoin's log price has risen roughly linearly in log time since the genesis block. P(t) = 10^a · t^b, with t in days since 2009-01-03 and b ≈ 5.7 (5.6 to 5.8 depending on the fit).
- **Santostasi's explanation:** the exponent comes from network physics. Address growth scales as t^3 (epidemic-style spreading). Price scales with addresses as N^1.84 (a generalised Metcalfe law). 3 × 1.84 ≈ 5.6.
- **Cycles and corridor:** price oscillates around the trend in roughly 4-year cycles tied to the halvings. The lower edge of the corridor (the floor, 0.42× trend) has held on a closing basis since October 2015. The upside overshoots have shrunk every cycle, and the last one barely cleared the trend.
- **Horizon:** this is a long-horizon model. Out of sample, it beats "price stays where it is" at 12–24 months but not at 1–6 months.
- **Its value to you:**
  1. A valuation regime map.
  2. A long-horizon drift and mean-reversion prior.
  3. Strike anchors, the floor above all.

  It is not a short-term timing signal.

### 0.2 Current state snapshot (2026-11-10)

| Item | Value |
|---|---|
| Trend (SP26 parameters, primary) | $156,340. Price is **0.471× trend** (residual −0.327 dex) |
| Zone | **DEEP_VALUE** (0.42–0.55× trend) |
| Floor (0.42 × SP26 trend) | **$65,663**, 12.2% below price |
| Floor zone across definitions | $61.1k (0.42 × live refit) / $65.7k (SP26) / $72.5k (2.5% quantile line, 1.6% below price) |
| Floor growth | +2.65% per 30 days (+36.4%/yr, about $1,740/month now) |
| Flat-price test date | If BTC goes nowhere, the rising floor catches it in **133 days (~2027-03-23)** |
| Percentile | Cheaper relative to trend than 95.7% of days since 2018, and 89% of all days |
| Cycle | ATH $126,272 intraday on 2025-10-06, only **1.16× trend**. Low $57,735 intraday on 2026-07-01 (close $58,525 on 06-30) at **0.415–0.421×**, i.e. on the floor. Drawdown −54%. 400 days since the ATH, ~133 since the low, ~17 months to the ~April 2028 halving |
| Realized vol | 36% (30d), 41% (90d), 44% (365d) |
| Baseline forecast (NvPL, §3) | $80.5k (2027-03-26), $98.4k (2027-06-25), $137.8k (2027-09-24), $184.9k (2027-12-31) |

### 0.3 Ten operating rules
1. **Horizon gate.** Use the power law as the *primary* thesis only for positions with **≥ 6 months** to expiry. At 3–6 months it is a secondary tilt. Under 3 months it is only a filter: never the reason for a short-dated directional trade.
2. **Baseline expectation = NvPL** (naive–power-law blend, §3). The calculator's `ar` numbers are an optimistic in-sample bound. Never size on them.
3. **The zone sets the menu of structures** (§7.2). DEEP_VALUE, the current zone, calls for long-dated bullish structures plus defined-risk put selling at or below the floor.
4. **Strike anchors:**
   - Put strikes: the floor at expiry and the cycle low.
   - Call-spread short strikes: 0.55× / 0.80× trend at expiry, or the NvPL forecast.
   - Call-selling zone: 1.25× trend.
   - **Do not use the old "resistance" bands (3–4× trend).** The last cycle peaked at 1.16–1.29×.
5. **Test every trade in three worlds** with the calculator:
   - `risk_neutral`: the model is wrong; zero drift at the market IV.
   - `nvpl`: the model holds.
   - `mix`: 50/50 by default.

   Take a trade only if it is attractive under `nvpl` **and** survivable under `risk_neutral`.
6. **Defined risk only for power-law-premised trades.** Use spreads and long options. No naked short puts or calls. Risk at most 2–3% of the account per trade and at most 15–20% in total across power-law-premised trades. Ladder expiries and entries.
7. **Expect path pain.** In real time, even entries below 0.5× trend saw a drawdown of more than 10% within 90 days about 30% of the time, and more than 20% about 10–13% of the time (worst −50%). Put strikes and sizes must survive a −25% excursion.
8. **Use today's volatility regime, not old cycles.** Realized vol is ~40% now, vs. 65–90% in earlier cycles. The last drawdown was −54% (not −80%) and the last peak was 1.16× trend (not 3–6×). Scale targets accordingly.
9. **Invalidation.** Drop the power-law bullish bias if any of these happens:
   - two consecutive weekly closes below the floor (0.42 × SP26 trend);
   - any daily close below 0.35× trend;
   - 30 or more days below the floor.

   Then close power-law-premised short puts. Resume only after two weekly closes back above the floor (§8).
10. **Refresh.** Run `powerlaw.py --live` before every trade decision and at least weekly. Every number in this brief is stale after 2026-11-10.

---

## 1. The theory, precisely

### 1.1 Equation, units, conversions
- **Equation:** `log10(P) = a + b·log10(t)`, equivalently `P = 10^a · t^b`.
- **Time:** t = days since the Genesis Block, **2009-01-03**, counted in UTC calendar days. On 2026-11-10, t = 6,520.
- **Residual:** `r = log10(P / trend)`, in **dex** (decimal exponent).
  - +0.1 dex = ×1.259
  - +0.30 dex ≈ ×2 (one SP26 sigma)
  - −0.377 dex = ×0.42 (the floor)
  - 1 dex = ×10
  - 1 dex = 2.3026 natural-log units
- **Trend growth rate:** `d ln P / dt = b / t`. It decays slowly:

  | When | Trend growth |
  |---|---|
  | Today | 0.0873%/day = 2.65%/30d = **36.4%/yr** |
  | 2028 | ~32%/yr |
  | 2030 | ~29%/yr |

- **Milestones of the SP26 trend:**

  | Level | Date |
  |---|---|
  | Trend crossed $100k | 2025-07-05 |
  | Trend reaches $200k | 2027-08-25 |
  | Floor reaches $100k | 2028-03-24 |
  | Trend reaches $1M | 2033-09-28 |

  Santostasi's public projections are $1M around 2033 and $10M around 2045.

### 1.2 Parameter sets you will meet (always say which one a number comes from)

| Name | a = log10 A | b | Origin | Trend on 2026-11-10 | Price/trend |
|---|---|---|---|---|---|
| **SP26 (primary)** | −16.509 | 5.690 | Santostasi & Perrenod 2026, OLS on 5,696 days (2010-07-17 → 2026-02-18), R² 0.961, σ 0.302 dex | $156,340 | 0.471 |
| MLE26 | −16.399 | 5.658 | Santostasi & Perrenod Oct-2026 skew-normal maximum likelihood (start t = 584) | $152,059 | 0.484 |
| CANON | −16.995 (A = 1.0117e-17) | 5.82 | Most widely copied "Santostasi formula" (2018–2024 charts, Bitbo and most calculators). Also equals the median quantile-regression slope (5.823) | $159,946 | 0.461 |
| REFIT (live) | −16.330 | 5.635 | OLS on all data to the evaluation date, no look-ahead; R² 0.962, σ 0.301 dex | $145,586 | 0.506 |
| QR 2.5% line | −17.546 | 5.874 | 2.5% quantile regression (Burger-style lower band) | $72,463 (a floor-type line) | 1.016 |

**Crosswalk of external charts:**

| Source | Parameters and bands |
|---|---|
| Newhedge (the Scientific Bitcoin Institute's production pipeline) | ≈ SP26 trend; floor 0.42× (Feb-2026: trend $121.7k, floor $51.1k) |
| btcpowerlaw.nl "Observatory" | 10^−16.493·t^5.688, floor 0.42× |
| bitcoinpower.law | 10^−16.493·t^5.68 |
| Burger (2019) | Power-law corridor (support and top lines), reported as 10^−17.016·t^5.845 |
| Porkopolis (Mezinskis) | Percentile bands at 2.5/97.5 and 16.5/83.5; exponent ≈ 5.77 |
| PlanC | 999-quantile regression. Argues the median decays but the 1% floor does not. Decay-adjusted fair value ≈ $100k vs. OLS $118–130k (Mar-2026) |
| TradingView "Power Law Corridor (Santostasi)" | b ≈ 5.845; floor ≈ 10th percentile; σ bands at 0.1615 |

Expect fair value to differ by ±5–10% between tools, and floors to differ more (0.35×–0.45× trend).

### 1.3 The corridor
- **Trend ("fair value").** The OLS line. In this brief, "trend" means SP26.
- **Floor = 0.42 × trend** (−0.377 dex ≈ −1.25σ), the Scientific Bitcoin Institute convention.
  - Closing breaches since 2011 happened only in May–June 2012 (13 days) and Aug–Oct 2015 (65 days); none since 2015-10-21.
  - Intraday wicks below it since 2015: 2016-08, **2020-03-13 (COVID, low 0.351×)**, 2022-11/12 and 2026-06/07.
  - The 2022 low closed at **0.422×** and the 2026 low at **0.421×**. The 2026 low came *after* the data window used to fit SP26 (it ends 2026-02-18), so it was a genuine out-of-sample hit.
- **Upper band.** Historical "resistance" lines (3–4× trend, or the 97.5% quantile at ~$320k today) are **obsolete** for trading. Cycle peaks as multiples of trend:

  | Year | Peak multiple |
  |---|---|
  | 2011 | 15.9× |
  | 2013 | 11.3× |
  | 2017 | 6.4× |
  | 2021 | 3.4× |
  | Dec-2024 | 1.29× |
  | Oct-2025 ATH | 1.16× |

  Treat **1.0–1.3× trend as the realistic top zone** for the next cycle unless the data say otherwise.
- **Sigma bands.** SP26's σ = 0.302 dex (a factor of 2 either side), but the residual σ over the last 4 years is only **0.14 dex**. The corridor is compressing.

### 1.4 Santostasi's causal story (what "the theory" claims)
1. **Adoption.** Non-zero-balance addresses grow as N(t) ∝ t^3.046 (R² 0.977).
   - His explanation: spreading through a scale-free social network produces cubic cumulative growth, following Colgate et al. 1989 and Bacchetti & Koch 1989 on AIDS case counts. Adoption starts with densely connected hubs, saturates them, then moves outward in a "saturation wave". The exponent 3 = 1 (initial ramp) + 1 (incubation convolution) + 1 (cumulative integration).
2. **Value.** Price grows as P ∝ N^1.838 (R² 0.951).
   - His explanation: a generalised Metcalfe law, between Sarnoff's N and Metcalfe's N², with the marginal value of each new user declining.
3. **Composition.** b = β_A × β_M = 3.046 × 1.838 = **5.60**, vs. 5.69 observed. The "theoretical" value is 3 × 2 = 6.
4. **Scale invariance.**
   - P(λt)/P(t) ≈ λ^b holds for BTC: the pair-ratio test is straight with binned R² 0.995 and b* = 5.59–5.62, while NASDAQ, the S&P 500 and gold curve.
   - Rolling b* has a median of 5.73 ± 0.58.
   - The Bayesian posterior is 5.729 (95% CI 5.703–5.754).
5. **Mining feedback (earlier, informal versions).** Price up → miner revenue up → hash rate up (roughly ∝ P² in his argument) → security up → adoption up → price up. Price, hash rate and addresses are all power laws of time and of each other.
6. **Interpretation.**
   - The trend is an *attractor* set by network growth. Cycles are halving-linked oscillations around it with decaying amplitude.
   - Bitcoin "behaves more like a growing organism or city (scaling laws) than a company".
   - The residuals are close to log-normal (skew +0.85) and, he argues, stationary.

### 1.5 Santostasi & Perrenod's falsification criteria (official)
- **F1.** Price stays more than 3σ below the fit (below 0.124× trend, about $19k today) for more than a year.
- **F2.** The address-growth exponent falls well below 3.
- **F3.** The exponent drifts outside [5.0, 7.0] over several years.
- **F4.** The Metcalfe-fit R² falls below 0.7.
- **F5.** The rolling 3-year R² stays below 0.80 for more than two years.

These are very loose: F1 tolerates a price ~70% below the floor (~88% below trend) for up to a year. For trading, use the tighter practical invalidation in §8.

### 1.6 Track record that matters for you
- **Floor: strong.** It held at the 2022 low (0.422×) and the 2026 low (0.421× close), the second out of sample.
- **Cycle top: weak.** Santostasi's widely reported projection was a peak of ~$210k around Dec-2025/Jan-2026, followed by a ~$60k low in 2026.
  - The low was right: $57.7k on 2026-07-01.
  - The top was ~40% too high: $126k on 2025-10-06.
- **Exponent drift.** The real-time OLS exponent fell from 5.85 (Nov-2022) to 5.64 (Nov-2026), because this cycle underperformed the trend.
- **Cycle mean.** The mean residual for the current halving epoch is −0.095 dex. Earlier epochs: +0.06, +0.02, 0.00.

---

## 2. Verified empirical facts (independent replication on daily closes, 2010-07-18 → 2026-11-10)

### 2.1 Fit quality and exponent stability
- **Full sample:** a = −16.330, b = 5.635, R² = 0.962, residual σ = 0.301 dex, skew +0.93.
- **Exponent by sample end:**

  | 2014 | 2016 | 2018 | 2020 | 2022 | 2024 | Feb-2026 | Nov-2026 |
  |---|---|---|---|---|---|---|---|
  | 6.85 | 5.62 | 5.87 | 5.74 | 5.82 | 5.70 | 5.69 | 5.63 |

- **Exponent by sample start:**

  | From 2011 | From 2012 | From 2013 | From 2015 | From 2017 |
  |---|---|---|---|---|
  | 5.49 | 5.56 | 5.24 | 5.84 | 4.81 |

  Santostasi & Perrenod's own table gives 5.00 when starting in 2018. The exponent is *not* window-invariant.
- **Quantile-regression slopes:**

  | Quantile | 1% | 2.5% | 5% | 50% | 95% | 97.5% | 99% |
  |---|---|---|---|---|---|---|---|
  | Slope | 5.95 | 5.87 | 5.87 | 5.82 | 4.90 | 4.75 | 4.64 |

  The lower edge rises faster than the median and the upper edge slower: **the corridor is converging**.
- **Autocorrelation.** Daily residual autocorrelation is ρ ≈ 0.998 (decorrelation ~440–530 days), so the effective number of independent observations is **≈ 13**. Santostasi & Perrenod concede this. Every "it has always happened" statement rests on **5–8 independent episodes**.

### 2.2 Cycle table (multiples of the SP26 trend)

| Cycle | Halving | Peak close | Peak × trend | Halving → peak | Cycle low (close) | Low × trend | Peak → low | Drawdown |
|---|---|---|---|---|---|---|---|---|
| 1 | 2012-11-28 | 2013-12-04 $1,135 | 11.1× (11.3× on 11-29) | 371 d | 2015-01-14 $176. Residual low came 2015-08-24 | 0.54× (Jan) / **0.378×** (Aug) | 406 d | ≈ −85% |
| 2 | 2016-07-09 | 2017-12-17 $19,250 | 6.2× (6.4× intraday) | 526 d | 2018-12-15 $3,185 | 0.57× | 363 d | −84% |
| 3 | 2020-05-11 | 2021-11-10 $64,756 | 2.7× (3.4× in Mar–Apr 2021) | 548 d | 2022-11-21 $15,778 | **0.422×** | 376 d | −76% (−78% intraday) |
| 4 | 2024-04-20 | 2025-10-06 $124,824 ($126,272 intraday) | **1.15×** (max 1.29× on 2024-12-17) | 534 d | 2026-06-30 $58,525 ($57,735 on 07-01) | **0.421×** | 267 d | −53% (−54% intraday) |

Other reference points:
- **COVID crash:** 2020-03-12 closed at 0.45×; the 2020-03-13 intraday low was 0.351×.
- **Pre-halving era:** 2011-06-08 peak at 15.9×; 2011-11 low at 0.44×.
- **Cycle low → next halving:** 513–542 days in earlier cycles. If the July-2026 low holds, this cycle's gap to the ~April-2028 halving would be ~650 days.

### 2.3 Four structural regularities (what is stable and what is not)
1. **The floor is stable.** Cycle lows: 0.378× (2015), 0.44× (2016), 0.57× (2018), 0.422× (2022), 0.421× (2026).
2. **Peaks are collapsing.** 15.9× → 11.3× → 6.4× → 3.4× → 1.29× (1.16× at the ATH).
3. **Volatility is compressing.**
   - Realized vol by year: 149% (2013), 93% (2017), 79% (2021), 65% (2022), 43% (2023), 52% (2024), 42% (2025), 44% (2026).
   - The 4-year rolling residual σ fell from 0.39 dex (2014) to 0.23 (2020–22) and 0.14 (2026).
4. **The cycle centre is drifting down.** Mean residual by halving epoch: +0.061, +0.015, −0.001, **−0.095**.

Together these say the corridor is narrowing toward roughly 0.42×–1.3× trend. The "true centre" may now be ~0.6–0.8× of the SP26 trend, consistent with PlanC's decay-adjusted fair value and with the falling real-time exponent.

**Implication:** the floor is the most reliable part of the model. Scale upside targets down.

### 2.4 How fast deviations revert: r(t+h) = c + k(h)·r(t) (in-sample)

| h (days) | 30 | 60 | 90 | 120 | 180 | 270 | 365 | 540 | 730 |
|---|---|---|---|---|---|---|---|---|---|
| k(h), from 2013 | 0.93 | 0.84 | 0.74 | 0.66 | 0.53 | 0.25 | −0.08 | −0.33 | −0.40 |
| Error σ, dex | 0.105 | 0.155 | 0.190 | 0.211 | 0.240 | 0.275 | 0.269 | 0.224 | 0.206 |
| k(h), from 2017 | 0.92 | 0.83 | 0.72 | — | 0.39 | — | −0.19 | — | — |

- The daily AR(1) half-life is about 1 year (325–370 days; ~220 days since 2018).
- k turns negative after about a year. That is the cycle oscillation: the residual tends to flip sign 12–24 months later.
- From today's residual of −0.327, the expected residual is −0.24 at 90 days, −0.17 at 180 days, and ~0 at 365 days, on top of trend growth.
- The error σ was measured in a higher-vol era. Today's equivalent is roughly 55–65% of the table values.
- This regression is the calculator's optimistic `ar` forecast.

### 2.5 What happened next, by valuation zone (real-time: monthly refits, entries from 2013-02, overlapping windows)

| Price / trend at entry | 30d median (P up) | 90d median (P up) | 180d median (P up) | 365d median (P up; 10th pct) |
|---|---|---|---|---|
| < 0.50× | +6% (71%) | +23% (77%) | +50% (90%) | **+130% (100%; +50%)** |
| 0.50–0.63× | +3% (57%) | +4% (56%) | +37% (75%) | +127% (92%; +14%) |
| 0.63–0.79× | +6% (63%) | +26% (71%) | +57% (80%) | +242% (92%; +35%) |
| 0.79–1.0× | +2% (52%) | +10% (63%) | +27% (72%) | +58% (73%; −25%) |
| 1.0–1.41× | +1% (52%) | −6% (43%) | −16% (36%) | −5% (47%; −46%) |
| 1.41–2.0× | +2% (53%) | +26% (65%) | −3% (49%) | −20% (45%; −64%) |
| > 2.0× | −7% (36%) | −22% (29%) | −18% (33%) | −45% (26%; −72%) |

- **In-sample (hindsight SP26 trend, entries since 2017), < 0.50× bucket:** 90d +35% (100% up), 180d +65% (100%), 365d +155% (100%). Hindsight inflates short-horizon reliability.
- **Discrete near-floor episodes** (residual < −0.33, i.e. below 0.468× trend) since 2012: eight, in 2012 (×2), 2015, 2016 (×2), 2020, 2022 and 2026.
  - 365-day forward returns of the seven completed episodes: +2608%, +1626%, +124%, +151%, +410%, +1056%, +133%.
  - 30-day returns ranged from −11% to +39%.
  - The current episode (from 2026-06-05) is +4% at 30 days and +33% at 90 days.
  - Episodes lasted 64–118 days, except COVID (5 days). Retests were common (2012, 2015–16, 2022).

### 2.6 Path risk after entering below 0.5× trend (real-time; relevant for put sellers)

| Window | Median worst close drawdown | 10th pct | 5th pct | Worst | P(dd < −10%) | P(dd < −20%) |
|---|---|---|---|---|---|---|
| 30d | −2.9% | −18.7% | −24.3% | −49.4% | 20% | 9% |
| 90d | −4.1% | −21.6% | −25.7% | −49.7% | **31%** | **13%** |
| 180d | −5.1% | −22.4% | −26.8% | −49.7% | 37% | 14% |
| 90d, since 2017 only | −3.7% | −19.6% | −21.8% | −26.0% | 28% | 9% |

The same statistic with hindsight (SP26) says P(dd < −10% within 90 days) = 4%. **Hindsight makes the floor look about 8× safer than it was in real time.**

### 2.7 Floor breaches: hindsight vs. real time
- **Hindsight (SP26):** 74 closing days below the floor since 2011 (1.3%), none after 2015-10-21.
- **Real time:**
  - The floor "broke" in 2014–15: the exponent fitted then was 6.5–6.9, so the floor line sat far too high.
  - The **Nov-2022 low printed 0.354× the floor line as it stood that day**. That line later moved down as more data came in.
  - The Jun-2026 low printed 0.431× in real time.

**Lesson:** part of the floor's spotless record comes from refitting. Treat a floor touch as a high-probability support zone, not a guarantee.

---

## 3. Which horizons the model actually forecasts (out-of-sample evidence)

### Baquero & Tinoco (2026), Naive–Power-Law blend (NvPL)
- **Forecast:** `log p̂(t+Δ) = (1 − w)·log p_t + w·log trend(t+Δ)`, with `w(Δ) = 1/(1 + e^(−0.015·(Δ − 274)))`.
- **Weights:** w ≈ 0.02 at 30 days, 0.06 at 90 days, 0.20 at 180 days, 0.80 at 365 days.
- **Test:** five non-overlapping two-year holdouts, 2016–2026.

RMSE (log10):

| Horizon | 1m | 3m | 6m | 12m | 18m | 24m | 36m |
|---|---|---|---|---|---|---|---|
| Naive (today's price) | 0.079 | 0.152 | 0.233 | 0.356 | 0.456 | 0.523 | 0.626 |
| NvPL | 0.080 | 0.152 | 0.229 | 0.336 | 0.407 | 0.437 | 0.447 |

- No model beat NvPL. The pure power law was excluded from the model confidence set; NvPL was retained.
- At 1–6 months the naive forecast could not be beaten. No model's directional accuracy beat the simple "always up" rule.
- The crossover horizon (where the trend starts to beat the current price) **shortened from ~9 months (2013–17) to ~4 months (2023–26)** as volatility fell.

### Baquero & Menezes (2026), "Weak structure, strong forecasts"
- The naive forecast is significantly better at 1–3 months.
- The power law is significantly better than every alternative at 18–24 months.
- A 3-sigmoid curve fits better in-sample (ΔAIC −2,547) but forecasts worst at long horizons.

### Bossi (Nov-2026)
- Expanding-window power law, log10 RMSE: 0.376 at 12 months and 0.356 at 24 months.
- Last price: 0.515 and 0.687. That is a typical error factor of 2.4 vs. 3.3.
- Whether the gap is statistically significant depends on the test.

### What this means for options
- **6–24 month expiries** are where the model carries information.
- **Under 3 months**, use market signals (flows, liquidations, the vol surface, news). The power law is only a filter there.

The calculator's `nvpl` column blends toward the live REFIT trend with no time shift. That is more conservative at long horizons than the paper's exact specification (a 240-day shift, which fits an exponent of ~6.27 and gives higher long-run values).

---

## 4. Criticisms and model risk (and how to account for them)

| Critique | Status | How to account for it |
|---|---|---|
| **Spurious regression / non-stationarity** (Tim Stolte, Marcel Burger at Amdax). Residuals are autocorrelated with ρ ≈ 0.998, giving ~13 effective observations | Conceded by Santostasi & Perrenod (Oct-2026). Their dependence-corrected tests cannot reject the power law (goodness-of-fit p = 0.15 at the published start, 0.78 at the selected cutoff; log-log curvature p = 0.55), but also cannot rank it against alternatives | Treat stated R² and confidence intervals as overstated. Size for model failure |
| **Window and origin sensitivity.** Shifting the origin 1,000 days changes b from 5.65 to 8.09; a post-2017 fit gives 4.8–5.0; a Feb-2026 preprint reported ~4.2 on 2011–2026 data | Real | Watch the REFIT exponent. If it trends below ~5.5, lower all targets (use REFIT, not SP26) |
| **Composition identity is near-algebraic** (Faulkner; Bossi via the Frisch–Waugh–Lovell theorem: 99.3–99.8% of β_A·β_M is the shared time trend). The epidemic analogy is post hoc; no Granger causality has been shown | Real | The mechanism story adds little predictive evidence. Trust the empirical regularities, not the narrative |
| **Bayesian stability analysis** treats autocorrelated rolling estimates as independent, making the posterior ~5.6× too narrow (Faulkner) | Real | Ignore the tight posterior |
| **Decaying returns.** Peaks are collapsing, the epoch-mean residual has turned negative, and the real-time exponent is falling. PlanC: the median decays, the floor does not | Supported by §2.3 | Upside targets: 0.55–1.0× trend, not 2–4×. Treat SP26 "fair value" as an upper-centre estimate |
| **Regime change.** ETF/institutional ownership; dollar-liquidity dependence (Bossi's rule uses the dollar trend); regulation; technology/security shocks | Unknown | Invalidation rules in §8. Defined risk only |
| **Survivorship and discovery bias.** The fit was "found" after the fact (2014/2018) on the asset that survived | Real | Prefer the out-of-sample results (§3) over in-sample tables |

**Bottom line:** the power law is a strong long-horizon prior and a good regime map. It is weak as structural "physics" and useless for 1–3 month timing. Its most dependable feature has been the floor, and the floor is being tested right now.

---

## 5. Current situation (2026-11-10) and scenarios

**Where we are:**
- Price is 0.47× trend after a −54% drawdown from a peak that reached only 1.16× trend.
- The 2026-07-01 low touched the floor exactly.
- The rebound peaked at a $86.5k close on 2026-10-04 (0.57×). Price has since faded to $73.7k, the lowest close since October began.

**The test:**
- The floor rises ~2.65% every 30 days. At a flat price it catches BTC around **2027-03-23**.
- So within ~4–5 months, either BTC resumes trend-like growth (~36%/yr) or it closes below the floor, for the first time since 2015 on a closing basis.
- Mar-2027 and Jun-2027 expiries carry the most model-specific information.

**Cycle clock (a heuristic, not part of Santostasi's power law):**
- If 2026-07-01 was the cycle low (~268 days after the ATH, shorter than the previous 363–406 days), compare the earlier floor-zone episodes: Aug-2015, Jan-2016 and Nov-2022 gained +124% to +151% over the next 12 months.
- If bottoms follow the older 363–406-day spacing from the ATH, that window is **2026-10-04 to 2026-11-16, i.e. now**. A retest of the floor zone is plausible.

**Scenarios** (no fabricated probabilities; quantify with the calculator under your live IV):

| Scenario | What happens | Consequences |
|---|---|---|
| **A. Floor holds, mean reversion** (power-law base case) | Retests of ~$66–74k hold through Q1-2027. Price recovers toward 0.55× trend (~$97–104k by Mar–Jun 2027) and then 0.8× (~$150–180k by late 2027) | NvPL path: $80.5k (Mar-27), $98.4k (Jun-27), $137.8k (Sep-27), $184.9k (Dec-27) |
| **B. Grind and floor test** | BTC ranges $60–85k. The floor catches it by ~Mar-2027, followed by two weekly closes below 0.42× | Invalidation. The decaying-trend camp is vindicated. Power-law-premised short puts are at risk |
| **C. Shock** | A macro or liquidity event drives a fast move to ≤ 0.35× trend ($54.7k today, $61.5k by 2027-03-26) | Model invalid for trading until the floor is reclaimed |

**Calculator check:** at IV 47% for the 2027-03-26 expiry, P(below the floor at expiry) is 56% risk-neutral, 38% NvPL and 47% mix. The lognormal does not model floor support, and historically breaches were much rarer than this (see §2.7), so read these as pessimistic for put sellers.

---

## 6. Forward levels at option expiries (as of 2026-11-10; refresh with the calculator)

| Expiry | Days | Floor 0.42× | QR 2.5% | 0.55× | 0.80× | Trend (SP26) | 1.25× | REFIT trend | NvPL forecast (w) | AR (optimistic) |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-27 | 17 | 66,643 | 73,580 | 87,270 | 126,939 | 158,673 | 198,341 | 147,738 | 74,732 (0.02) | 76,927 |
| 2026-12-25 | 45 | 68,283 | 75,451 | 89,419 | 130,064 | 162,579 | 203,224 | 151,339 | 75,336 (0.03) | 83,790 |
| 2027-01-29 | 80 | 70,381 | 77,845 | 92,165 | 134,059 | 167,573 | 209,467 | 155,942 | 76,571 (0.05) | 93,467 |
| 2027-03-26 | 136 | 73,847 | 81,806 | 96,704 | 140,661 | 175,826 | 219,783 | 163,546 | 80,547 (0.11) | 108,329 |
| 2027-06-25 | 227 | 79,779 | 88,599 | 104,473 | 151,960 | 189,950 | 237,438 | 176,551 | 98,352 (0.33) | 136,397 |
| 2027-09-24 | 318 | 86,099 | 95,853 | 112,748 | 163,997 | 204,997 | 256,246 | 190,396 | 137,762 (0.66) | 189,736 |
| 2027-12-31 | 416 | 93,360 | 104,210 | 122,257 | 177,828 | 222,285 | 277,857 | 206,291 | 184,917 (0.89) | 238,757 |
| 2028-03-31 | 507 | 100,547 | 112,503 | 131,669 | 191,519 | 239,398 | 299,248 | 222,013 | 214,914 (0.97) | 274,250 |
| 2028-06-30 | 598 | 108,185 | 121,337 | 141,671 | 206,066 | 257,583 | 321,979 | 238,707 | 236,558 (0.99) | 300,311 |
| 2028-12-29 | 780 | 124,898 | 140,736 | 163,557 | 237,901 | 297,377 | 371,721 | 275,201 | 275,018 (1.00) | 351,472 |

Reading the table:
- The floor at an expiry is the price the model says BTC should *not* close below by then.
- NvPL is a horizon-specific point forecast, **not a path**: its weight jumps between 6 and 12 months.
- Given §2.3, read 1.25× trend as a ceiling zone, not a target.

---

## 7. Options playbook

### 7.1 Principles
1. **Physical vs. risk-neutral.** Option prices embed roughly zero drift (r ≈ funding) plus the market's vol and skew. The power law is a *physical* forecast: trend growth of ~36%/yr plus mean reversion from 0.47× trend. Any edge you have is that drift and the floor, and both show up mainly at long tenors.
   - Main vehicle: calls and call spreads with 6–14 months to expiry.
   - Floor monetisation: selling put spreads struck at or below the floor.
2. **Match the horizon** (rule 1). With 30–90 days to expiry, the NvPL weight on the trend is only 2–6%.
3. **Structure by IV.**
   - High IV (after flushes, IV well above realized): sell put spreads, or finance call spreads by selling the upper leg or put spreads.
   - Low or quiet IV near the floor: buy long-dated calls or call spreads outright.
4. **Skew.** After selloffs BTC puts carry more IV than calls. Selling downside skew below the floor and buying upside call spreads gives a risk-reversal-style structure that matches the model's view.
5. **Liquidity.** Long-dated OKX/Deribit options have wide markets. Work limit orders near mid, and check open interest.

### 7.2 Zone → structure map (zone = price / SP26 trend; `powerlaw.py` prints it)

| Zone | Historical context | Power-law bias | Preferred structures | Avoid |
|---|---|---|---|---|
| **BELOW_FLOOR** (< 0.42) | 1.3% of days since 2011; no closes since 2015. Every past case led to large 12-month gains, or this time the model is broken | Long-term bullish but unconfirmed | Small 9–15m bull call spreads, staged entries. Sell puts only after a reclaim (two daily closes back above the floor) | Short puts, leverage, large size |
| **DEEP_VALUE** (0.42–0.55): **current** | 365d median +130% (100% up, ~7 episodes). 90d +23% (77% up). Floor retests common | Strong at 6–24 months; neutral under 3 months | 6–14m bull call spreads; 2–5m short put spreads struck at or below the floor or cycle low; put-spread-financed call spreads; diagonals (long 9–12m call, short 1–2m OTM call) when the term structure pays | Short-dated puts as directional bets; short calls near spot; expecting a quick move to trend |
| **VALUE** (0.55–0.80) | 365d median +127% to +242% (92% up) | Bullish | 3–9m call spreads; roll long calls up and out; put spreads near the floor | Naked short calls |
| **FAIR** (0.80–1.25) | 365d +58% (0.8–1.0×) and −5% (1.0–1.41×). The last cycle topped at 1.15–1.29× | Neutral; take profits | Scale out of long calls at 0.8× and 1.0×. Collars; call spreads sold at ≥ 1.25× trend; condors with short put ≥ 0.6× and short call ≥ 1.3× | New aggressive long-dated calls |
| **EXTENDED** (1.25–2.0) | 365d median −20% (45% up) | Bearish tilt | Bear call spreads at 1.5–2×; 3–9m put spreads; protect spot | Long OTM calls |
| **BUBBLE** (> 2.0) | 365d median −45% (26% up). May never recur given peak decay | Bearish | 6–12m put spreads; sell call spreads; cut long delta | Any long calls |

### 7.3 Playbook for now (DEEP_VALUE, 12% above the floor, floor rising 2.65%/month)
1. **Core long thesis (≥ 6 months).** Bull call spreads on the Jun-27, Sep-27 and Dec-27 expiries.
   - Long strike: ~spot to +20% ($75–90k).
   - Short strike: about 0.55× trend at expiry ($104k Jun-27, $113k Sep-27, $122k Dec-27) or the NvPL forecast.
   - Spread the position across 2–3 expiries and enter in 2–3 tranches: now, on a floor-zone retest, and on a reclaim of 0.55×.
2. **Floor monetisation (2–5 months).** Short put spreads.
   - Short strike at or below the lower of the current floor ($65.7k) and the cycle low ($57.7k). Long strike 10–15% lower.
   - These lose only if the floor breaks decisively, which is the falsification event itself.
   - Enter after long-liquidation flushes or IV spikes.
   - Take profit at 50–70% of max; close on invalidation.
3. **Low-cost bullish combo (6–9 months).** Sell a put spread below the floor at expiry and use the credit to buy a call spread.
4. **Floor-retest plan.** The floor will be ~$68.3k at the Dec-25 expiry and ~$73.8k at the Mar-26 expiry, and the cycle low is $57.7k.
   - Retests happened in 2012, 2015–16 and 2022 (2022-11-21 low, then a 2022-12-30 wick).
   - A flush into $66–70k with long-liquidation cascades (btcliq) is the power law's preferred point to add bullish exposure.
   - Two weekly closes below the floor flip you to §8.
5. **Avoid now:**
   - Buying short-dated puts as a directional bet. They are the priciest part of the surface after a selloff, and the model says downside is limited unless the floor breaks.
   - Naked short puts: gap risk through the floor.
   - Short-dated OTM calls bought for a quick mean reversion.
   - 3–4× trend "resistance" as a call target.

### 7.4 Worked evaluation (illustrative; flat IV 47% assumed, live chain IV and skew will differ)

`python3 powerlaw.py --offline --expiry 2027-03-26 --strikes 50000,55000,60000,65000,80000,100000 --iv 0.47`

Mar-26-2027 expiry (136 days):
- Median price at expiry: $70.7k risk-neutral, $80.5k NvPL, $108.3k AR.
- P(touch the floor before expiry): 82% risk-neutral, 72% NvPL, 77% mix.

| Strike | P(above): rn / nvpl / mix | Put value: rn / nvpl / mix |
|---|---|---|
| 50,000 | 88.6% / 95.2% / 91.9% | 706 / 260 / 483 |
| 60,000 | 71.6% / 84.8% / 78.2% | 2,641 / 1,206 / 1,924 |
| 65,000 | 61.5% / 77.3% / 69.4% | 4,311 / 2,150 / 3,231 |

**Short 60k/50k put spread** (per 1 BTC notional):
- Credit ≈ $1,935; max loss ≈ $8,065.
- Expected payout: $946 under NvPL, so expected profit ≈ +$989. Under risk-neutral it is ≈ 0 by construction. Under mix, ≈ +$494.
- P(short strike finishes in the money): 28% risk-neutral, 15% NvPL.

`python3 powerlaw.py --offline --expiry 2027-06-25 --strikes 80000,130000 --iv 0.47`

**80k/130k call spread, Jun-25-2027** (227 days):
- Cost ≈ $7,428 (Black-Scholes); max payoff $50,000.
- Expected value: $22,179 under NvPL, $14,804 under mix.
- P(BTC above 80k at expiry): 34% risk-neutral, 71% NvPL, 53% mix.

**How to read this:** the model-vs-market gaps are large. That mostly measures how much the market disagrees with the model, i.e. model risk. **Size by the risk-neutral loss (the premium or the spread's max loss), never by the NvPL gain.** Use the live mark for each leg, re-run with that strike's IV, and include fees and spread costs.

### 7.5 Sizing and risk rules
- At most 2–3% of the account at risk per power-law-premised trade, and at most 15–20% in total. No naked short options. No leverage on spot to "buy the floor".
- If you use Kelly sizing, compute it under the **mix** measure and take at most ¼ of it. With ~13 effective observations, model uncertainty dominates.
- Ladder expiries (for example Mar/Jun/Sep/Dec-2027) and entries. Keep dry powder for floor retests.
- Pre-commit exits:
  - Close short puts at 50–70% of max profit, or immediately on invalidation.
  - Scale long calls or call spreads at 0.55×, 0.80× and 1.0× trend.
  - Do not wait for 1.25× or more, given peak decay.
- Log every decision with the calculator's JSON snapshot (zone, ratio, cushion, IV) for later review.

### 7.6 Combining with short-term signals (e.g. the btcliq chart: liquidation map, OI by venue, OKX chain OI/IV/max pain, order books, news)

The power law sets **direction bias, strike zones and expiries**. Microstructure sets **timing**:
- Add bullish power-law structures after long-liquidation cascades into the floor zone, when IV spikes and the book absorbs.
- Avoid opening short puts just before scheduled macro events.
- Use large-OI strikes as pins only for short-dated hedges.
- Treat Telegram/X headlines as catalysts for entries, not as reasons to override the zone.

### 7.7 Anti-patterns
- Using the trend ($156k) as a 1–6 month target.
- Trusting in-sample "100% of the time" statistics: there are ~7 episodes and hindsight is built in.
- Mixing up parameterisations (CANON vs. SP26 vs. REFIT) between tools.
- Forgetting that the floor rises every day, so a fixed-dollar "floor" goes stale within weeks.
- Treating a floor touch as a guarantee (see the real-time path risk in §2.6).
- Converting dex ↔ % incorrectly (0.1 dex = 25.9%).

### 7.8 New strategy ideas consistent with the model (paper-trade or backtest with `--asof` first)
1. **Zone-scaled core plus options overlay.**
   - Long delta sized by zone: high at < 0.55×, medium at 0.55–1.0×, low above 1.25×.
   - Covered calls or call spreads above 1.25× trend.
   - Put spreads struck below the floor at expiry.
2. **Floor-ladder put selling.** Sell 30–120-day put spreads struck at or below the floor at expiry, only when the ratio is below 0.6× and IV rank is high. Stop on invalidation.
3. **Model-vs-market scan.** Each week, run the calculator across the OKX chain (each strike with its own IV) and log the largest NvPL-vs-mark dislocations. Trade only persistent ones, at small size.
4. **Dollar-liquidity overlay** (Bossi 2026, "Little Rule"): exposure from the trend of a JPY/CNY/GBP basket against the USD, plus the power-law deviation.
   - In-sample, 2014-01 to 2026-11: CAGR 70% vs. 43% for buy-and-hold; max drawdown −52% vs. −84%.
   - The parameters were chosen in-sample and the gains are not statistically separable from buy-and-hold except for the drawdown. Research only.

---

## 8. Invalidation and monitoring

**Weekly (and on any ±10% move), run:**
```
python3 powerlaw.py --live --json
```
Record: zone, ratio, floor cushion, flat-price test date, REFIT exponent, and the 30/90-day realized vol.

| Trigger | Action |
|---|---|
| Daily close below the floor (0.42 × SP26) | Warning. No new short puts. Tighten stops on power-law-premised positions |
| **Two consecutive weekly closes below the floor**, OR 30+ days below the floor, OR any daily close below 0.35× trend | **Invalidation.** Drop the power-law bullish bias. Close power-law-premised short puts. Cut long-call exposure to lottery size. Re-enable only after two weekly closes back above the floor |
| REFIT exponent below 5.5, or a new cycle peak below 1.0× trend | Downgrade upside targets: use REFIT trend × 0.8 as the centre |
| Ratio crosses 0.55 / 0.80 / 1.0 upward | Take profits per §7.5. Move to the next zone's menu |
| Ratio above 1.25 | Stop adding long delta. Start selling calls and buying put spreads |

---

## 9. Calculator reference (`/home/miller/Desktop/claude-project/btc-powerlaw/powerlaw.py`)

The script uses the Python standard library only. Data comes from the Coin Metrics community API (daily `PriceUSD` from 2010-07-18), cached in `data/btc_daily.csv` and auto-updated. If the network fails it falls back to the cache. `--live` takes the OKX BTC-USD index (falling back to Coinbase) as today's price.

```
python3 powerlaw.py --live                                     # state + forward levels at upcoming expiries
python3 powerlaw.py --live --json                              # machine-readable output
python3 powerlaw.py --live --expiry 2027-06-25 --strikes 60000,80000,100000 --iv 0.47 [--trust 0.5] [--rate 0.0]
python3 powerlaw.py --asof 2022-11-21                          # what the model said then (refit uses only data to that date)
python3 powerlaw.py --dates 2027-02-26,2027-04-30              # forward levels at custom dates
```

**Key JSON fields:**

| Field | Meaning |
|---|---|
| `primary.zone` | Price / SP26-trend zone |
| `primary.ratio` | Price / SP26 trend |
| `primary.resid_dex` | log10 of the ratio |
| `primary.floor`, `primary.floor_cushion_pct` | Floor and % distance to it |
| `primary.floor_qr025`, `primary.floor_refit` | Alternative floor lines |
| `primary.flat_price_meets_floor_on` | Date a flat price meets the floor |
| `primary.pct_rank_*` | Residual percentile vs. history |
| `models.*` | All parameter sets; `REFIT` has the live exponent |
| `cycle.*` | ATH and post-ATH low, with ratios, days since, halving clock |
| `halving_epochs[]` | Peak and trough ratios and mean residual per epoch |
| `realized_vol` | rv30, rv90, rv365 |
| `forward[]` | Per date: floor, QR 2.5% line, 0.55/0.80/1.25× trend, trend, REFIT trend, NvPL forecast and weight, AR forecast and k |
| `options` | median_price, p_touch_floor_before_expiry, p_below_floor_at_expiry, and per-strike p_above, call and put values under `risk_neutral` (Black-Scholes at the given IV), `nvpl`, `ar` and `mix` |

**Method notes:**
- Model distributions are lognormal around the NvPL or AR log-forecast, with dispersion IV·√T.
- Floor-touch probabilities use a constant-drift Brownian barrier against the rising floor, which is approximate.
- No floor-support behaviour is modelled, so floor-breach probabilities are conservative.

---

## 10. Sources
- **Santostasi & Perrenod (2026).** "A Mechanistic Derivation of the Bitcoin Price Power Law: Network Adoption Dynamics and Generalised Metcalfe Scaling." Zenodo preprint, 2 Apr 2026 (https://zenodo.org/records/19387099). Reported as published in Elsevier's *Nonlinear Science*, online 29 Jun 2026.
- **Santostasi & Perrenod (2026).** "A Clauset-in-Spirit Goodness-of-Fit Test for a Power Law in Time: Application to Bitcoin, 2010–2026." Zenodo, 2 Oct 2026 (https://zenodo.org/records/23105856).
- **Santostasi.** "The Bitcoin Power Law Theory" (Medium, Jan 2024), and Reddit posts (Mar 2014 on Metcalfe; Sep 2018 on the price-time power law). The Medium page was not retrievable for this brief; its content is superseded by the 2026 papers, which cite it.
- **Baquero & Tinoco (2026).** "The Naive–Power Law Blend as a Robust Baseline for Bitcoin Price Forecasting." Zenodo (https://zenodo.org/records/22678659).
- **Baquero & Menezes (2026).** "Bitcoin's Power Law: Weak Structure, Strong Forecasts." arXiv:2605.21316.
- **Baquero (2026).** "Bitcoin Price Prediction: Peer-Reviewed Evidence and Social Media Discourse." arXiv:2606.00071.
- **Faulkner (2026).** "Description Without Prediction: A Methodological Critique of Bitcoin Price Power Law Models." Zenodo (https://zenodo.org/records/19975701).
- **Bossi (2026).** "Shared Trends and the Bitcoin Power Law: Forecasting and Network Evidence" (https://zenodo.org/records/23961927), and "Bitcoin: The Little Rule That Beats the Market" (https://zenodo.org/records/24092037).
- **Perrenod (Mar 2026).** "Bitcoin's Strong Persistence: Structure Revealed" (Substack): QR median slope 5.862, Hurst H ≈ 0.95 for the residuals, log-periodic λ ≈ 2.07.
- **Burger (2019).** "Bitcoin's natural long-term power-law corridor of growth" (hcburger.com/blog/powerlaw).
- **btcpowerlaw.nl.** "Who Found the Bitcoin Power Law?" (literature review and parameter table).
- **CryptoSlate (20 Feb 2026).** "If Bitcoin stays near $67k, it breaks the Power Law floor by mid-December" (Newhedge floor figures).
- **CoinDesk via cryptonews (3 Jun 2026).** "Bitcoin hits Power Law level low that historically precedes a rebound."
- **Data.** Coin Metrics community API (PriceUSD); Bitstamp daily OHLC for intraday highs and lows; OKX index for the live price.
