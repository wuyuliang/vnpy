# Cycle V1 AG Candidate Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate eight auditable AG candidate review cards and a precise explanation of `LARGE_CYCLE_UNAVAILABLE` inside backtest run `20260830_091651_20260101_20260205_30m_5m_1m`.

**Architecture:** Add one isolated CLI module that reads an existing cycle_v1 report, reloads the report's audited normalized symbol, rebuilds the configured 30-minute long-cycle frame, and derives daily/60-minute/1-minute review windows. Keep diagnosis and rendering as pure helpers so focused tests can verify event matching, output dimensions, and artifact counts without vendor access.

**Tech Stack:** Python 3.13, pandas, Pillow, pytest, existing cycle_v1 session aggregators and replay-frame builder.

---

### Task 1: Candidate Diagnosis

**Files:**
- Create: `cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py`
- Create: `cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py`

- [ ] **Step 1: Write the failing diagnosis test**

Add a test that supplies two candidates, their candidate-linked rejection rows,
and a synthetic long-cycle frame. Assert that `_diagnose_candidates` orders by
signal time and binds each rejection to the latest completed large snapshot:

```python
def test_diagnose_candidates_uses_latest_visible_large_snapshot() -> None:
    candidates = pd.DataFrame(
        {
            "candidate_id": ["later", "early"],
            "signal_time": [
                "2026-01-28 13:47:00+08:00",
                "2026-01-16 00:30:00+08:00",
            ],
            "active_time": [
                "2026-01-28 13:49:00+08:00",
                "2026-01-16 00:32:00+08:00",
            ],
            "symbol": ["AG", "AG"],
            "contract_code": ["AG2604.SHF", "AG2604.SHF"],
            "setup": ["H1", "H2"],
            "direction": [1, 1],
            "cycle": ["BULL_TIGHT_CHANNEL", "BULL_BROAD_CHANNEL"],
            "entry": [29402.0, 22911.0],
            "stop": [29350.0, 22879.0],
            "target": [29456.0, 22994.0],
        }
    )
    rejections = pd.DataFrame(
        {
            "candidate_id": ["early", "later"],
            "feature_asof": [
                "2026-01-16 00:31:00+08:00",
                "2026-01-28 13:48:00+08:00",
            ],
            "reason_code": ["LARGE_CYCLE_UNAVAILABLE"] * 2,
            "detail": ["large_cycle=UNAVAILABLE"] * 2,
        }
    )
    long_frame = pd.DataFrame(
        {
            "feature_asof": pd.to_datetime(
                ["2026-01-16 00:30:00+08:00", "2026-01-28 11:30:00+08:00"]
            ),
            "cycle": ["UNAVAILABLE", "UNAVAILABLE"],
            "reason": [
                "INSUFFICIENT_CAUSAL_HISTORY",
                "INSUFFICIENT_PRESSURE_HISTORY",
            ],
        }
    )

    result = _diagnose_candidates(candidates, rejections, long_frame)

    assert result["candidate_id"].tolist() == ["early", "later"]
    assert result["large_reason"].tolist() == [
        "INSUFFICIENT_CAUSAL_HISTORY",
        "INSUFFICIENT_PRESSURE_HISTORY",
    ]
```

- [ ] **Step 2: Run the diagnosis test and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py::test_diagnose_candidates_uses_latest_visible_large_snapshot -q
```

Expected: FAIL because `candidate_charts` and `_diagnose_candidates` do not yet exist.

- [ ] **Step 3: Implement minimal diagnosis logic**

Create `candidate_charts.py` with constants for the required report columns and
implement `_diagnose_candidates`. Parse all event timestamps as Asia/Shanghai-aware,
require exactly one `LARGE_CYCLE_UNAVAILABLE` rejection per candidate, select the
last long-frame row whose `feature_asof` is not later than rejection
`feature_asof`, and reject missing or non-`UNAVAILABLE`/`TRANSITION` snapshots.
Return rows sorted by signal time with `sequence`, `large_cycle`, `large_reason`,
and `diagnostic_source="recomputed_30min_snapshot"`.

- [ ] **Step 4: Run the diagnosis test and verify GREEN**

Run the command from Step 2. Expected: `1 passed`.

- [ ] **Step 5: Commit the diagnosis unit**

```bash
git add cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py
git commit -m "feat: diagnose cycle candidate rejections"
```

### Task 2: Three-Timeframe Review Card

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py`

- [ ] **Step 1: Write failing chart-window and rendering tests**

Use deterministic OHLCV frames with timezone-aware `DatetimeIndex` values. Test
that `_select_event_window` returns at most the requested before/after counts,
then render one card and assert its dimensions:

```python
def test_render_candidate_card_has_three_panel_dimensions() -> None:
    row = _diagnosed_candidate_row()
    daily = _bars("2026-01-01", periods=30, frequency="1D")
    hourly = _bars("2026-01-10 09:00", periods=80, frequency="1h")
    minute = _bars("2026-01-12 12:30", periods=180, frequency="1min")

    image = render_candidate_card(row, daily=daily, hourly=hourly, minute=minute)

    assert image.size == (1680, 1240)
    assert image.getbbox() == (0, 0, 1680, 1240)
```

- [ ] **Step 2: Run rendering tests and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py -q
```

Expected: diagnosis test passes and rendering test fails because
`render_candidate_card` is absent.

- [ ] **Step 3: Implement the minimal Pillow renderer**

Implement `_select_event_window` using `DatetimeIndex.searchsorted`, with daily
`20/5`, hourly `36/12`, and minute `80/40` before/after counts. Implement
`render_candidate_card` with the existing Brooks report palette and the existing
`_draw_candlestick_panel` helper. Add overlays for `SIGNAL`, `ACTIVE`, entry,
stop, and target; shade the region after the signal and label it
`POST-EVENT REVIEW ONLY`. The header must state
`60min visual context; strategy large_tf=30min` and show the large snapshot
reason.

- [ ] **Step 4: Run rendering tests and verify GREEN**

Run the command from Step 2. Expected: all tests in
`test_candidate_charts.py` pass.

- [ ] **Step 5: Commit the rendering unit**

```bash
git add cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py
git commit -m "feat: render candidate timeframe cards"
```

### Task 3: Report Artifacts and Current AG Run

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py`
- Generate: `cta/strategy/brooks/report/cycle_v1/20260830_091651_20260101_20260205_30m_5m_1m/candidate_charts/index.csv`
- Generate: `cta/strategy/brooks/report/cycle_v1/20260830_091651_20260101_20260205_30m_5m_1m/candidate_charts/*.png`
- Generate: `cta/strategy/brooks/report/cycle_v1/20260830_091651_20260101_20260205_30m_5m_1m/LARGE_CYCLE_UNAVAILABLE.md`

- [ ] **Step 1: Write the failing artifact-generation test**

Monkeypatch `_load_chart_context` to return synthetic daily, hourly, minute, and
long frames. Build a temporary report with `summary.json`, `candidates.csv`, and
`rejections.csv`, call `generate_candidate_charts`, and assert exact outputs:

```python
def test_generate_candidate_charts_writes_index_images_and_explanation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = _write_report_fixture(tmp_path, candidate_count=2)
    monkeypatch.setattr(candidate_charts, "_load_chart_context", _chart_context)

    index = candidate_charts.generate_candidate_charts(report_dir)

    assert len(index) == 2
    assert len(list((report_dir / "candidate_charts").glob("*.png"))) == 2
    assert (report_dir / "candidate_charts" / "index.csv").exists()
    explanation = (report_dir / "LARGE_CYCLE_UNAVAILABLE.md").read_text()
    assert "INSUFFICIENT_CAUSAL_HISTORY" in explanation
    assert "60min is review context" in explanation
```

- [ ] **Step 2: Run the artifact test and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py -q
```

Expected: artifact test fails because `generate_candidate_charts` is absent.

- [ ] **Step 3: Implement report loading, aggregation, writing, and CLI**

Implement `_load_chart_context` to read `summary.json`, load the exact metadata
cache with `MetadataBundle.load`, reload the sole requested symbol using
`load_normalized_symbol`, build configured replay frames with
`build_symbol_replay_frames`, aggregate daily bars with
`aggregate_completed_daily_bars`, and aggregate completed 60-minute bars with
`aggregate_completed_bars(minutes=60)`. Implement `generate_candidate_charts`
to write deterministic filenames, `index.csv`, and the Markdown explanation.
Add `main()` with required `--report-dir` and optional `--data-root` arguments.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all candidate chart tests pass.

- [ ] **Step 5: Generate the eight current-run charts**

Run:

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.candidate_charts \
  --report-dir cta/strategy/brooks/report/cycle_v1/20260830_091651_20260101_20260205_30m_5m_1m
```

Expected: JSON summary with `candidate_count=8`, `image_count=8`, and no missing
panel or diagnosis errors.

- [ ] **Step 6: Verify generated artifacts and regression tests**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py cta/strategy/brooks/cycle_v1/tests/test_cycle_cli_runner.py cta/strategy/brooks/cycle_v1/tests/test_cycle_scanner.py -q
```

Expected: all selected tests pass. Open a representative early-history chart
and a pressure-history chart with `view_image`; verify legible labels, three
non-empty panels, exact signal placement, and the post-event warning.

- [ ] **Step 7: Commit implementation and generated text/index artifacts**

```bash
git add cta/strategy/brooks/cycle_v1/backtest/candidate_charts.py cta/strategy/brooks/cycle_v1/tests/test_candidate_charts.py docs/superpowers/plans/2026-08-30-cycle-v1-ag-candidate-charts.md
git commit -m "feat: add AG candidate review charts"
```

PNG and report-directory artifacts remain local backtest outputs unless the
repository's existing ignore policy explicitly tracks them.
