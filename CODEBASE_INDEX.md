# Codebase index

| Module | Responsibility |
|---|---|
| `app.py` | Sidebar, timed scan fragment, compact AgGrid table and lazy detail panels |
| `movers.py` | Pure rolling-window metrics and explicit sustained-mover qualification |
| `movers_ui.py` | Strong Movers controls, validated URL settings, table and active-tab rendering |
| `market_relative.py` | Pure BTC/peer benchmark calculation, coverage rules and relative qualification |
| `relative_ui.py` | Market Relative controls, summary metrics and independent table state |
| `window_data.py` | Per-session 15-minute scan snapshot shared by rolling-window tabs |
| `pair_details.py` | Shared lazy selected-pair funding/OI panel |
| `services.py` | Process-shared Streamlit resource factories |
| `binance_client.py` | HTTP timeouts/retries, weighted and endpoint budgets, coalesced source caches, contract filtering, aligned completed candles, optional derivatives |
| `scoring.py` | Pure activity score, eligibility, active thresholds and observed direction |
| `grid_view.py` | Nine visible numeric columns, display-only formatters, stable symbol IDs and sort/selection state |
| `ui_helpers.py` | URL validation, literal filters, tick precision and UTC formatting |
| `history_store.py` | Non-destructive schema migration, fresh observations, deduplicated events and indexed forward-return outcomes |
| `tests/` | Deterministic exchange fixtures and behavioral tests; explicit browser fixture |
| `live_check.py` | Read-only bounded live integration check |

## Interfaces

- `BinanceClient.scan(min_volume=10_000_000, interval='5m', force=False) -> Scan`.
  A scan contains its DataFrame, common candle boundary, timestamped source
  results, errors and an explicit empty-result reason.
- `BinanceClient.details(symbol) -> dict[str, Source]`: funding, last two
  settlements and 13 five-minute OI observations. Called only on explicit demand.
- `score_activity(frame) -> DataFrame`: adds activity, eligible, active,
  direction and status; ranks only fresh/complete observations with acceptable spread.
- `HistoryStore.record(frame,timeframe,min_volume,now=None) -> dict`: event and
  observation counts plus an error field. `summary(...)` reports matching
  configuration outcomes and recent events, never predictive confidence.

The obsolete statistical scoring and history-adjusted ranking interfaces have
been removed. Old verification command filenames remain as test entry points.

- `BinanceClient.scan_movers(min_volume=10_000_000, force=False) -> Scan`:
  raw rows with window-prefixed metrics for 1h/4h/8h/1d and source status.
- `qualify_movers(frame, window, min_return, min_relative_volume,
  min_efficiency, hold_pct, max_spread) -> DataFrame`: chosen-window metrics,
  qualified flag, observed direction and explicit failure reasons. No score.
- `app.py` gates stateful native tabs before invoking their timed fragment;
  inactive tabs do not scan. Strong Movers never calls HistoryStore.
- `compare_market(frame, window, boundary, now, min_volume, percentile,
  min_gap, mode) -> RelativeResult`: returns the filtered altcoin universe,
  BTC and peer benchmarks, coverage, qualification and explicit reasons.
  Market Relative never calls HistoryStore.
