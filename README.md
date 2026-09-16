# Goal xG — Over 0.5 FT / product-xG (Big-5)

Lean typed Python package for Alessandro’s **Over 0.5 FT** product index **xG 0–100**
(`xg_score = round(100 × p)`), with CLI + **FastAPI** live UI.

## Data sources

| Layer | Client | Role |
|-------|--------|------|
| Live clock / score / events | `goal_xg/clients/goal_ws.py` | WebSocket (`wss://api.goal-api.com/ws`) |
| Fixtures / statistics / history | `goal_xg/clients/goal_api.py` | REST — stats densified **only** on 0-0 in 28–32′ |
| Shot-stat **enrichment** | `goal_xg/clients/api_sports.py` | API-Sports v3 — fills missing tiri/SoT/xG/… |
| Coach **identity** (name / id) | `goal_xg/clients/football_data.py` | Soft feature; not H2H |
| Weather (optional) | Open-Meteo | Fail-closed omit + renorm if missing |

Auth (env only — **never commit**):

| Variable | Required | Notes |
|----------|----------|-------|
| `GOAL_API_KEY` | Yes (live/CLI/web data) | Bearer for GOAL REST + WS |
| `API_SPORTS_KEY` or `APISPORTS_KEY` | Optional | API-Football v3 enrich (`x-apisports-key`) |
| `FOOTBALL_DATA_API_KEY` or `FOOTBALL_DATA_TOKEN` | Optional | football-data.org coach identity |

## Locked product rules

| Item | Lock |
|------|------|
| Event | Over 0.5 FT (anti 0-0) |
| Live trigger | ≈30′ 1H, still **0-0** (window **28–32′**) |
| Score | Product xG 0–100 = `round(100 × P(Over 0.5 FT \| 0-0 @ 30′))` |
| Leagues | Big-5 only |
| Football | **GOAL API** primary (REST + WS); **API-Sports** fills shot gaps |
| MVP omit + renorm | `live_ratings`, `coach_h2h`; missing shot fields omit + renorm |

## Setup

Requires **Python 3.11+**. You **must** set `GOAL_API_KEY` in `.env` for live GOAL REST/WS data (never commit the real key). Optional: `FOOTBALL_DATA_API_KEY` for coach identity.

### Windows (PowerShell)

Easiest — one script (creates `.venv` if needed, installs deps, copies `.env.example` → `.env` only if missing, starts the server):

```powershell
cd code\goal-xg
# If scripts are blocked once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\scripts\run-web.ps1
# or double-click / run:
.\scripts\run-web.bat
```

Manual steps:

```powershell
cd code\goal-xg
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,live]"
copy .env.example .env
# edit .env — set GOAL_API_KEY=... (required for live data)
uvicorn goal_xg.web.app:app --host 127.0.0.1 --port 8000
```

### Linux / macOS

```bash
cd code/goal-xg
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,live]"
cp .env.example .env
# edit .env — set GOAL_API_KEY (never commit)
```

## Web UI (FastAPI)

```bash
# from code/goal-xg with .env loaded (and venv activated)
uvicorn goal_xg.web.app:app --host 127.0.0.1 --port 8000
# or:
goal-xg-web
# or:
python3 -m goal_xg.web
```

On Windows you can use the same `uvicorn` / `goal-xg-web` commands after activating `.venv`, or run `.\scripts\run-web.ps1`.

Then open `http://127.0.0.1:8000/` — live Big-5 cards + 0-0 @30′ candidates.
Fixture detail: `/fixtures/{id}` · JSON: `/api/live`, `/api/live30/{id}` · `/health`.

Without `GOAL_API_KEY` the UI still serves `/` and `/health` (alert / `goal_api_key_configured: false`). **Set `GOAL_API_KEY` in `.env` before expecting live fixtures.**

## CLI

```bash
goal-xg leagues
goal-xg fixtures --date 2026-09-15
goal-xg score-prematch --fixture-id 12345678
goal-xg watch-live --list-only
goal-xg watch-live --list-only --score
goal-xg score-live30 --fixture-id 12345678
goal-xg coach --team-id 86
```

## Package layout

```text
goal_xg/
  clients/          # GOAL REST/WS + football-data.org
  features/         # prematch priors + extractors (form/streaks/…)
  live30/           # Phase B window / stats / score / service
  model/            # weights + dynamic shifts + calibration + xG
  backtest/         # historical 0-0@30′ harness (Brier / reliability)
  web/              # FastAPI + Jinja templates + static
  cli.py
```

## Tests (no API key)

```bash
PYTHONPATH=. pytest -q
```

## Backtest (0-0 @ 30′ → FT)

Offline harness with Brier / log-loss / reliability bins (mocks or JSONL):

```bash
# sample fixture shipped in tests/
python -m goal_xg.backtest tests/fixtures/backtest_sample.jsonl
# predictions-only JSONL rows with p_hat + y_over05:
python -m goal_xg.backtest path/to/preds.jsonl --predictions-only
```

See `goal_xg/backtest/` and `tests/test_backtest.py`.

## Phase status

- **A:** pre-match priors + extractors (form, streaks, …) + CLI.
- **B:** WS + live30 scoring + league calib hook + CLI.
- **Web:** FastAPI dashboard — **one** `/fixtures/live` per refresh (quota fix).
- **Still omitted:** coach-vs-coach H2H; live player ratings; Open-Meteo; VPS deploy.
