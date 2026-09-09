# Activity scanner

Binance USDT perpetual activity screening. Defaults: $10m 24h quote volume,
5-minute completed candles, manual refresh. No API keys required.

## Windows setup and run

From this folder, with Python 3.12+ and uv available:

```powershell
uv --cache-dir .uv-cache venv .venv --python 3.12
uv --cache-dir .uv-cache pip install --python .venv/Scripts/python.exe -r requirements.txt
.venv/Scripts/python.exe -m streamlit run app.py
```

The existing `.venv` is already configured on this machine. Exact tested direct
and transitive versions, including pytest, are locked in `requirements.txt`.
Streamlit 1.63 is required for lazy expander open-state gating.
The server binds to localhost; open http://127.0.0.1:8501.

## Verify

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q
.venv/Scripts/python.exe -X utf8 live_check.py
```

All pytest checks are offline, deterministic, and use temporary databases.
The live check uses bulk endpoints and candles for only the top three pairs by
volume, and reports unavailable/partial results separately from a successful check.
It does not write history. Older check-script names delegate to the new tests.

Browser-only fixture (synthetic data, never the production entry point):

```powershell
.venv/Scripts/python.exe -m streamlit run tests/browser_app.py --server.port 8507
```

Verify all nine columns at desktop width, numeric header sorting, selection
and sort retention after refresh, tiny-price precision, literal `[` search,
empty results, timeframe changes, and collapsed details making no derivatives calls.
The fixture's database is under ignored `test-results/`.

## Data interpretation

Relative volume/trades compare the latest completed candle to the medians of
60 preceding complete, contiguous candles. Score is a weighted average of
cross-pair percentile ranks: 40% relative volume, 30% relative trades, 30%
absolute candle return. Rank ties use their average; at least two eligible
pairs are needed. Score is a screening heuristic, not an expected return.

Ranking requires complete fresh data and spread <=0.10%. Active requires
score >=80, relative volume >=1.5 and relative trades >=1.25. Quotes/prices
older than 120 seconds are excluded; zeros never substitute for missing data.
Wide-spread and unavailable pairs remain below ranked pairs with explanations.
In Manual mode observations age until the user refreshes; changing a display
filter does not fetch new market data.

Live bulk sources cache for 60s; universe metadata for an hour. Completed
candles are cached by symbol/timeframe/boundary. Optional pair funding and OI
cache for five minutes and are fetched only when a pair is selected and its
details are expanded. OI is base quantity over an exact one-hour window.

## Persistence and migration

`scanner_history.sqlite3` remains the default store; `SCANNER_HISTORY_DB` can
override it. First v2 use creates a `.pre-activity-v2.sqlite3` backup if the
legacy snapshots exist. Legacy tables remain unchanged and are excluded from
all new results. `scanner_meta` records schema version 2.

Fresh observations are deduplicated by symbol/source timestamp. Activity events
are deduplicated by symbol/timeframe/bar and spaced by one hour per
symbol/timeframe. Events carry scoring version and configuration; history shows
only matching settings over the last 24 wall-clock hours. New data retain 72h.

Outcomes compare actual forward price to detection price. The closest same-symbol
observation within 45–75 minutes is used; later wins a tie. Exact-hour readings
can resolve immediately; other events wait until 75 minutes so a better reading
can arrive. Missing data expires as unmeasurable. Flat prices are a resolved
outcome and count in the continuation-rate denominator. No statistical confidence
guarantee or ranking multiplier is derived from history. Returns exclude costs.

## Operational notes

Rate limits and caches are shared inside one Python process. Separate processes
or unrelated clients using the same IP share Binance's limits but not local
accounting. Used-weight headers and global 429/418 cooldowns provide backoff.
Metadata and limit changes should be checked against Binance documentation.
Storage errors appear in the UI; a successful scan does not imply history saved.

## Strong Movers

Open the Strong Movers tab for rolling 4h, 8h, and 1d windows. It defaults to
4h and Qualified only. Shared volume, literal search, and Manual/timed refresh
controls apply to the active tab only. Each tab retains its own sorting and selection.

Balanced qualification: absolute return at least 2%/3%/5%, relative quote volume
at least 1.25, efficiency at least 0.35, and both the completed close and fresh
current price in the favorable outer 20% of the window range. Daily volume is
at least $10m and spread at most 0.10%. Thresholds are adjustable. These are
screening defaults, not validated signals, probabilities, or a composite score.

Return is last completed close / first open - 1. Relative volume compares the
measured window with the median of 14 prior non-overlapping equal windows.
Efficiency is absolute net change / the absolute path through first open and
subsequent closes. The holding test uses the full candle high-low range;
the table's distance column instead reports current-price distance to the
favorable extreme as a percent of that extreme, clamped at zero beyond it.

All windows end at the same latest completed 15-minute boundary. One 1,440-bar
request per included symbol supplies up to 15 days of history and costs weight
10. Window, direction, threshold, and table changes reuse fetched histories.
Only the two newest cached payloads per symbol are retained, plus the existing
bounded last-success fallback. Initial scans can be slower than Activity and
remain subject to shared budgets. Stale, invalid, or insufficient histories
cannot qualify; turn off Qualified only to see reasons in row tooltips.
Source-age validation allows one second of clock rounding, never older than 120s.
Strong Movers does not write to or display Activity outcome history.

Share links preserve tab, window, direction, qualification toggle, and bounded
thresholds; old URLs still default to Activity. Price formatting remains numeric
and uses exchange tick size. Return sorting compares absolute magnitude.

Bounded live verification for this tab:

```powershell
.venv/Scripts/python.exe -X utf8 live_check.py --movers
```

## Market Relative

Market Relative compares eligible altcoins with BTCUSDT and other liquid alts
over 1h, 4h, 8h, or 1d. The default is 4h Relative strength, Qualified only.
It uses the same completed 15-minute snapshot as Strong Movers, so switching
between these tabs or windows does not repeat candle requests.

For each altcoin, the peer benchmark is the median eligible return after
excluding that row. The headline median includes every eligible altcoin.
Return percentile uses average ranks for ties. Strength requires percentile
at least 80 and a return gap over both BTC and peers of at least 0.5/1/1.5/2
percentage points for 1h/4h/8h/1d. Weakness uses symmetric conditions.
Thresholds are adjustable and retained in shareable URLs.

BTC history is included even when BTC falls below the display volume filter.
Qualification requires at least 20 eligible alts, at least 80% valid-return
coverage, aligned candles, fresh prices and quotes, and spread at most 0.10%.
Relative volume is informational: flat prices, zero volume baselines, and
missing participation history do not invalidate an otherwise valid return.
Search and Qualified only are display filters and never alter benchmarks.

Market Relative does not use Z-scores, produce a composite score, or write to
Activity history. Its measurements are descriptive screening inputs, not a
claim of positive trading expectancy.

Bounded live verification uses roughly the top 22 liquid contracts:

```powershell
.venv/Scripts/python.exe -X utf8 live_check.py --relative
```
