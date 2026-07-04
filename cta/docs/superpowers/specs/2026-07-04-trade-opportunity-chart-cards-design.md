# Trade Opportunity Chart Cards Design

## Context

The source trade opportunities are in:

`cta/backtest/20260627_ab_bigger_01_allowlist/oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv`

The file contains 16,238 rows. Every row should be rendered, not only executed trades.

Local OHLCV data exists for the symbols needed by this dataset under:

- `cta/data/feature/day/<symbol>/*.parquet`
- `cta/data/feature/minute60/<symbol>/*.parquet`

The current Python environment has `pandas`, `pyarrow`, and `Pillow`, but does not have `matplotlib`, `mplfinance`, `kaleido`, or `cairosvg`. To avoid dependency installation and keep the work reproducible, chart images will be generated directly with Pillow.

## Goal

Create a reproducible chart-rendering workflow that writes review images to `cta/analysis/`.

Success criteria:

- Render all 16,238 trade-opportunity rows.
- Sort the rendered opportunities by profit from high to low.
- Each output image shows weekly, daily, and hourly K-line context.
- Each chart panel includes volume bars below price candles.
- Each image displays key trade metadata, especially execution status and return/PnL fields.
- Generate an index CSV mapping source row, sort rank, trade metadata, and image path.

## Scope

In scope:

- Add a standalone script under `cta/analysis/`.
- Read the trade CSV as input.
- Load local day and minute60 OHLCV parquet data.
- Aggregate weekly bars from day bars.
- Render one PNG review card per trade row.
- Write a sorted `index.csv`.
- Record rows with missing chart data in the index instead of failing the whole run.

Out of scope:

- Using GUI trading software or manual screenshots.
- Downloading missing market data.
- Modifying source trade files or source OHLCV data.
- Adding new third-party dependencies.
- Refactoring existing CTA model/backtest code.

## Output Layout

The default output directory will be:

`cta/analysis/20260628_bigger_01_allowlist_trade_charts/`

Expected files:

- `charts/<rank>_<symbol>_<entry_datetime>_<side>_<status>.png`
- `index.csv`
- `render_summary.json`

The rank prefix is based on descending profit sort order, so the highest-profit opportunity appears first.

## Sorting

Primary sort:

- `net_pnl` descending, parsed as numeric.

Fallback sort if `net_pnl` is missing or non-numeric:

- `trade_return_pct` descending.

Tie-breakers:

- `entry_datetime` ascending.
- Source row number ascending.

## Image Design

Each PNG will be a single review card with:

- Header: rank, symbol, exchange, source row, entry time, signal time, source interval, side, signal type.
- Status block: `execution_status`, `block_reason`, `exit_reason`, `entry_action`, `exit_action`.
- Price block: `entry_fill_price` or `entry_price`, `final_exit_price`, `stop_loss_price`, `planned_exit_price`.
- Performance block: `net_pnl`, `gross_pnl`, `trade_return_pct`, `gross_return_pct`, `cost_pct`.
- Model/risk block: `trade_filter_prob`, `trade_filter_prob_pctl`, `final_decision_score`, `trade_filter_gate_score`, `trade_filter_gate_threshold`, `position_scale`, `position_notional`.
- Three chart panels: weekly, daily, hourly.
- Volume subplot beneath each chart panel.
- Vertical markers for entry time and final exit time where they fall inside the visible chart window.

Candles:

- Rising candle: red outline/fill.
- Falling candle: green outline/fill.
- Doji/flat candle: neutral gray.

Windows:

- Weekly: approximately 80 weekly bars ending after the trade exit when available.
- Daily: approximately 160 daily bars around the trade.
- Hourly: approximately 240 minute60 bars around the trade.

## Data Handling

Day bars:

- Prefer `cta/data/feature/day/<symbol>/*.parquet`.
- Use only core OHLCV columns: `datetime`, `open`, `high`, `low`, `close`, `volume`.

Hourly bars:

- Prefer `cta/data/feature/minute60/<symbol>/*.parquet`.
- Use only core OHLCV columns: `datetime`, `open`, `high`, `low`, `close`, `volume`.

Weekly bars:

- Aggregate from day bars using week-ending grouping.
- `open`: first.
- `high`: max.
- `low`: min.
- `close`: last.
- `volume`: sum.

Missing data:

- If one panel has no data, render a placeholder panel showing the missing reason.
- If all panels are missing, still write the row to `index.csv` with status `missing_all_chart_data` and skip the PNG.

## Implementation Shape

Add one script:

`cta/analysis/render_trade_opportunity_charts.py`

The script will expose a CLI:

```bash
python3 cta/analysis/render_trade_opportunity_charts.py \
  --trade-csv cta/backtest/20260627_ab_bigger_01_allowlist/oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv \
  --output-dir cta/analysis/20260628_bigger_01_allowlist_trade_charts
```

Optional CLI flags:

- `--limit N` for smoke testing only.
- `--start-rank N` for partial reruns.
- `--overwrite` to replace existing PNGs.

## Verification

Minimum verification:

- Run a smoke render with `--limit 5`.
- Confirm 5 PNG files are generated.
- Confirm `index.csv` has 5 rendered rows.
- Confirm rank order is descending by `net_pnl`.
- Run the full render.
- Confirm `index.csv` contains 16,238 rows.
- Confirm `render_summary.json` reports total input rows, rendered image count, skipped count, and missing-data counts.

## Assumptions

- "收益" means `net_pnl` unless unavailable, then `trade_return_pct`.
- "小时K线" maps to local `minute60` data.
- All rows in `all_trade_details.csv` are trade opportunities and should be included.
- Output PNGs generated from local OHLCV data are acceptable substitutes for GUI trading-software screenshots.
