# Trade Opportunity Chart Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible `cta/analysis` script that renders all 16,238 trade opportunities as profit-sorted PNG review cards with weekly, daily, and hourly K-line panels.

**Architecture:** Add one CLI script with small pure helpers for sorting, filename generation, OHLCV loading, weekly aggregation, window selection, and Pillow rendering. Add focused tests for the pure helpers so the risky ordering and aggregation behavior is pinned down before the full render. The script writes images, `index.csv`, and `render_summary.json` under the requested output directory without modifying source trade or market data.

**Tech Stack:** Python 3, pandas, pyarrow, Pillow, pytest.

---

### Task 1: Add Helper Tests

**Files:**
- Create: `cta/analysis/tests/test_render_trade_opportunity_charts.py`
- Later implementation target: `cta/analysis/render_trade_opportunity_charts.py`

- [ ] **Step 1: Write tests for sorting, filenames, and weekly aggregation**

Create `cta/analysis/tests/test_render_trade_opportunity_charts.py`:

```python
from datetime import datetime

import pandas as pd

from cta.analysis.render_trade_opportunity_charts import (
    aggregate_weekly_bars,
    make_chart_filename,
    sort_trade_rows,
)


def test_sort_trade_rows_orders_by_net_pnl_descending() -> None:
    rows = [
        {"source_row": 2, "entry_datetime": "2024-01-03 00:00:00", "net_pnl": "10", "trade_return_pct": "0.1"},
        {"source_row": 1, "entry_datetime": "2024-01-02 00:00:00", "net_pnl": "20", "trade_return_pct": "0.2"},
        {"source_row": 3, "entry_datetime": "2024-01-01 00:00:00", "net_pnl": "", "trade_return_pct": "0.3"},
    ]

    result = sort_trade_rows(rows)

    assert [row["source_row"] for row in result] == [1, 2, 3]
    assert [row["sort_rank"] for row in result] == [1, 2, 3]


def test_make_chart_filename_sanitizes_key_fields() -> None:
    row = {
        "sort_rank": 7,
        "symbol": "AL0",
        "entry_datetime": "2024-01-02 21:00:00",
        "side": "long",
        "execution_status": "blocked/final decision",
    }

    filename = make_chart_filename(row)

    assert filename == "000007_AL0_20240102_210000_long_blocked_final_decision.png"


def test_aggregate_weekly_bars_uses_ohlcv_rules() -> None:
    daily = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-08"]),
            "open": [10.0, 11.0, 20.0],
            "high": [12.0, 15.0, 21.0],
            "low": [9.0, 10.0, 19.0],
            "close": [11.0, 14.0, 20.5],
            "volume": [100.0, 150.0, 200.0],
        }
    )

    weekly = aggregate_weekly_bars(daily)

    assert len(weekly) == 2
    assert weekly.iloc[0]["open"] == 10.0
    assert weekly.iloc[0]["high"] == 15.0
    assert weekly.iloc[0]["low"] == 9.0
    assert weekly.iloc[0]["close"] == 14.0
    assert weekly.iloc[0]["volume"] == 250.0
    assert weekly.iloc[1]["open"] == 20.0
```

- [ ] **Step 2: Run tests to verify they fail before implementation**

Run:

```bash
python3 -m pytest cta/analysis/tests/test_render_trade_opportunity_charts.py -q
```

Expected: FAIL because `cta.analysis.render_trade_opportunity_charts` does not exist yet.

### Task 2: Implement Chart Rendering Script

**Files:**
- Create: `cta/analysis/render_trade_opportunity_charts.py`

- [ ] **Step 1: Add the script with pure helper functions and CLI**

Implement:

- `parse_float(value: object) -> float | None`
- `sort_trade_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]`
- `make_chart_filename(row: Mapping[str, object]) -> str`
- `aggregate_weekly_bars(day_bars: pd.DataFrame) -> pd.DataFrame`
- `load_symbol_bars(data_root: Path, interval: str, symbol: str) -> pd.DataFrame`
- `select_window(bars: pd.DataFrame, entry_dt: pd.Timestamp, exit_dt: pd.Timestamp | None, target_count: int) -> pd.DataFrame`
- Pillow drawing helpers for metadata, candles, volume, and entry/exit markers.
- `render_all(...) -> dict[str, object]`
- `main(argv: Sequence[str] | None = None) -> int`

The CLI must support:

```bash
python3 cta/analysis/render_trade_opportunity_charts.py \
  --trade-csv cta/backtest/20260627_ab_bigger_01_allowlist/oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv \
  --output-dir cta/analysis/20260628_bigger_01_allowlist_trade_charts \
  --limit 5 \
  --overwrite
```

- [ ] **Step 2: Run helper tests**

Run:

```bash
python3 -m pytest cta/analysis/tests/test_render_trade_opportunity_charts.py -q
```

Expected: PASS.

### Task 3: Smoke Render

**Files:**
- Generated: `cta/analysis/20260628_bigger_01_allowlist_trade_charts_smoke/`

- [ ] **Step 1: Render first five profit-sorted rows**

Run:

```bash
python3 cta/analysis/render_trade_opportunity_charts.py \
  --trade-csv cta/backtest/20260627_ab_bigger_01_allowlist/oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv \
  --output-dir cta/analysis/20260628_bigger_01_allowlist_trade_charts_smoke \
  --limit 5 \
  --overwrite
```

Expected:

- `render_summary.json` reports `total_input_rows` as `16238`.
- `index.csv` has 5 data rows.
- `charts/` contains 5 PNG files unless a row has no chart data at all.

- [ ] **Step 2: Verify smoke output programmatically**

Run:

```bash
python3 - <<'PY'
import csv
import json
from pathlib import Path

root = Path("cta/analysis/20260628_bigger_01_allowlist_trade_charts_smoke")
summary = json.loads((root / "render_summary.json").read_text())
with (root / "index.csv").open(newline="") as f:
    rows = list(csv.DictReader(f))
pngs = sorted((root / "charts").glob("*.png"))
print("summary", summary)
print("index_rows", len(rows))
print("pngs", len(pngs))
assert summary["total_input_rows"] == 16238
assert len(rows) == 5
assert len(pngs) == summary["rendered_images"]
PY
```

Expected: prints summary counts and exits with code 0.

### Task 4: Full Render

**Files:**
- Generated: `cta/analysis/20260628_bigger_01_allowlist_trade_charts/`

- [ ] **Step 1: Render all rows**

Run:

```bash
python3 cta/analysis/render_trade_opportunity_charts.py \
  --trade-csv cta/backtest/20260627_ab_bigger_01_allowlist/oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv \
  --output-dir cta/analysis/20260628_bigger_01_allowlist_trade_charts \
  --overwrite
```

Expected:

- `index.csv` has 16,238 data rows.
- PNG files are named with profit rank prefixes.
- `render_summary.json` records rendered, skipped, and missing-panel counts.

- [ ] **Step 2: Verify full output**

Run:

```bash
python3 - <<'PY'
import csv
import json
from pathlib import Path

root = Path("cta/analysis/20260628_bigger_01_allowlist_trade_charts")
summary = json.loads((root / "render_summary.json").read_text())
with (root / "index.csv").open(newline="") as f:
    rows = list(csv.DictReader(f))
pngs = sorted((root / "charts").glob("*.png"))
print("summary", summary)
print("index_rows", len(rows))
print("pngs", len(pngs))
assert len(rows) == 16238
assert len(pngs) == summary["rendered_images"]
assert summary["rendered_images"] + summary["skipped_images"] == 16238
PY
```

Expected: prints summary counts and exits with code 0.

### Task 5: Record Change Log

**Files:**
- Modify: `cta/report/change_log.md`

- [ ] **Step 1: Append a concise entry**

Append:

```markdown
## 2026-07-04 trade opportunity chart cards

- Added `cta/analysis/render_trade_opportunity_charts.py` to render all OOT trade opportunities into profit-sorted PNG review cards.
- Added helper tests in `cta/analysis/tests/test_render_trade_opportunity_charts.py`.
- Output directory: `cta/analysis/20260628_bigger_01_allowlist_trade_charts/`.
- Verification: helper tests, smoke render, and full output count checks.
```

- [ ] **Step 2: Check touched files**

Run:

```bash
git status --short cta/analysis cta/report/change_log.md cta/docs/superpowers/plans/2026-07-04-trade-opportunity-chart-cards-implementation.md
```

Expected: only the new analysis files, this plan, generated output directory, and the change log entry appear in these paths.
