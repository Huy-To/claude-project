# claude-project

BTC trading and research workspace.

| Folder | What it is |
|---|---|
| `btcliq/` | BTC liquidation heatmap + OKX trading dashboard with safety rails (see `btcliq/README.md`, `btcliq/SAFETY_RAILS.md`) |
| `btc-powerlaw/` | Bitcoin power-law model and brief |
| `garch-eval/` | Evaluation of the GARCH volatility method against DVOL |
| `garch-method-fixed/` | Local fork of the GARCH method Claude Code plugin, with bug fixes |
| `xnews/` | X/RSS news monitor (`xnews.zip` is an earlier copy of its source) |

Secrets (`.env`, `trading.toml`) and local data (`btcliq/data/`, `xnews/xnews/data/`) are not in this repo.
Copy each project's `.env.example` / `*.example.toml` to set it up.
