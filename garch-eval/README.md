# garch-method plugin audit (2026-11-13)

Independent check of `garch-method@garchmethod` (github.com/milesdeutscher/garchmethod, commit e1e93d9).
`report.txt` + `followup_output.txt` hold the full results; `results.json` the key numbers.

Re-run (needs a venv with arch, pandas, numpy, statsmodels):

    python -I eval_garch.py ~/.claude/plugins/cache/garchmethod/garch-method/1.0.0/scripts \
        btc_yf_snapshot_2026-11-13.csv ../btc-powerlaw/data/btc_daily.csv .
