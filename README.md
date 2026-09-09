# Binance USDT perpetual activity scanner

Streamlit / AgGrid screening dashboard for unusual activity in Binance USDT
perpetual futures. No API keys required — read-only public endpoints.

## Tabs

- **Activity** — 5-minute completed candles; cross-pair percentile score
  (40% relative volume, 30% relative trades, 30% absolute candle return).
  Active requires score >= 80, relative volume >= 1.5 and relative trades >= 1.25.
- **Strong Movers** — 1h/4h/8h/1d rolling windows with return, relative volume,
  efficiency and holding qualification. Reasons are shown in row tooltips.
- **Market Relative** — return gaps of eligible altcoins against BTC and
  leave-one-out peer medians over 1h/4h/8h/1d. No Z-scores, composite score,
  or history writes.

Each tab keeps its own sorting, selection, and shareable URL settings.

## Setup and run (Windows)

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv):

```powershell
uv --cache-dir .uv-cache venv .venv --python 3.12
uv --cache-dir .uv-cache pip install --python .venv/Scripts/python.exe -r requirements.txt
.venv/Scripts/python.exe -m streamlit run app.py
```

Open http://127.0.0.1:8501. Defaults: $10m adjustable 24h volume filter,
completed 5-minute candles, manual refresh.

## Verify

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q
.venv/Scripts/python.exe -X utf8 live_check.py
```

All pytest checks are offline and deterministic against isolated fixtures and
temporary databases. The live check is read-only and bounded. Bounded live
verification per tab: `live_check.py --movers` and `live_check.py --relative`.

## Data interpretation

Scores and qualifications are screening heuristics computed from actual price
returns and completed candles. Missing, invalid, and stale data are never
treated as zeros and cannot produce active events. Nothing here is a
probability estimate, a validated signal, or trading advice.

See `WORKFLOW.md` for data interpretation details and `CODEBASE_INDEX.md` for
the module map.
