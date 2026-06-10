# invest-A Configuration Guide

Configuration layers (highest priority first):

1. **Per-run flags** — CLI arguments passed to `/invest-A`
2. **Environment variables** — set in `.env` or shell environment
3. **Config YAML files** — `skills/invest-A/config/*.yaml`
4. **Defaults** — hardcoded in `data_pipeline.py`

---

## Per-run Flags

| Flag | Description | Example |
|------|-------------|---------|
| `{code}` | Stock/ETF code (required) | `600519`, `00700`, `510300` |
| `--compare {code}` | Side-by-side comparison | `--compare 000858` |
| `--with-macro` | Include macro analysis | `--with-macro` |
| `--dim {list}` | Limit dimensions | `--dim=finance,valuation` |
| `--deep` | Extended verification | `--deep` |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values:

| Variable | Required | Purpose | Registration |
|----------|----------|---------|--------------|
| `TUSHARE_TOKEN` | No | Tushare Pro API (A/HK stock data) | [tushare.pro](https://tushare.pro) |
| `FRED_API_KEY` | No | FRED macro data (recommended) | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) |
| `TAVILY_API_KEY` | No | Tavily AI search (1000 free/month) | [tavily.com](https://tavily.com) |
| `BOCHA_API_KEY` | No | Bocha Chinese search | [open.bocha.cn](https://open.bocha.cn) |

**All environment variables are optional.** When not configured, the skill falls back to free data sources (akshare/efinance/yfinance/WebSearch) and reports the degradation.

---

## Config YAML Files

Located at `skills/invest-A/config/`:

| File | Purpose |
|------|---------|
| `source_credibility.yaml` | Data source trust scores (★1-5), source groups for cross-validation |
| `dimension_baselines.yaml` | Dimension weights, minimum viable sources per dimension |
| `cross_validation_rules.yaml` | Agreement/divergence thresholds, timeliness penalty rules |

---

## Per-Harness Install Patterns

### Claude Code (CLI)

```bash
# Install via npx
npx skills add . -g -y

# Or symlink for live development
ln -sfn "$PWD/skills/invest-A" ~/.agents/skills/invest-A
```

### Claude Code (Desktop/Web)

Use the Claude Code plugin marketplace to install `invest-A`.

### Gemini CLI

```bash
gemini extensions install ./gemini-extension.json
```

### Codex / Cursor / GitHub Copilot

Install from the [Agent Skills](https://agentskills.io) marketplace or via `npx skills add`.

---

## Dependency Management

invest-A uses `uv` for isolated dependency management:

```bash
# Install uv (one-time)
brew install uv        # macOS
# or: curl -LsSf https://astral.sh/uv/install.sh | sh

# Create isolated virtual environment
uv sync

# All Python commands use the venv
uv run python -m skills.invest-A.scripts.lib.env_check
uv run pytest
```

The `.venv/` directory is git-ignored. Each developer runs `uv sync` once to create their own isolated environment. System Python stays clean.
