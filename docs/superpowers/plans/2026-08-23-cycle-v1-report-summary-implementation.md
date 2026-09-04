# Cycle V1 Report Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an answer-first trading summary with total PnL and return metrics to every cycle_v1 Markdown report.

**Architecture:** Extend the existing authoritative performance dictionary with total net PnL, total return, and final equity. Render a formatted summary from that dictionary before the funnel, preserving existing CSV schemas and detailed performance output.

**Tech Stack:** Python 3.13, pandas, pytest, Markdown report generation.

---

### Task 1: Add Authoritative Summary Metrics

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_reporting_research.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/reporter.py`

- [ ] **Step 1: Add failing metric assertions**

In `test_net_performance_and_group_reports_include_zero_trade_days_and_costs`, assert:

```python
assert metrics["total_net_pnl"] == pytest.approx(3.0)
assert metrics["total_return"] == pytest.approx(0.05)
assert metrics["final_equity"] == pytest.approx(105.0)
```

- [ ] **Step 2: Verify RED**

Run the focused test and expect missing-key failures for the three metrics.

- [ ] **Step 3: Return the three metrics from `compute_performance_metrics`**

Use the already-computed final equity and total return, and sum trade-level `net_pnl`. Do not derive total PnL from equity because cash flows may be added in future.

- [ ] **Step 4: Verify GREEN**

Run the focused performance test and expect it to pass.

### Task 2: Render Trading Summary First

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_reporting_research.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/reporter.py`

- [ ] **Step 1: Add failing Markdown assertions**

Extend the Markdown report test with all summary fields and assert:

```python
assert report.index("## Trading Summary") < report.index("## Funnel")
assert "- trade_count: 2" in report
assert "- total_net_pnl: 3,602.55" in report
assert "- total_return: 1.80%" in report
assert "- win_rate: 100.00%" in report
assert "- final_equity: 203,602.55" in report
```

Add a zero-trade case asserting `0`, `0.00`, `0.00%`, and `N/A` for undefined ratios.

- [ ] **Step 2: Verify RED**

Run the two Markdown tests and expect failure because `Trading Summary` does not exist.

- [ ] **Step 3: Implement summary rendering**

Render official status, then `Trading Summary`, then funnel. Use fixed field order and format cash to two decimals, percentages to two decimals, ratios to four decimals, and missing/non-finite values as `N/A`.

- [ ] **Step 4: Verify GREEN**

Run all reporting tests and expect them to pass.

### Task 3: Verify And Update The Referenced Report

**Files:**
- Modify: `cta/strategy/brooks/report/cycle_v1/20260823_171629_20260101_20260727_30m_5m_1m/summary.json`
- Modify: `cta/strategy/brooks/report/cycle_v1/20260823_171629_20260101_20260727_30m_5m_1m/report.md`
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`

- [ ] **Step 1: Run regression checks**

Run cycle_v1 tests, legacy scalp tests, Ruff, and compileall. All must pass.

- [ ] **Step 2: Update the saved summary and Markdown**

For the referenced report, record `total_net_pnl=3602.55`, `total_return=0.01801275`, and `final_equity=203602.55` in `summary.json`. Add the generated top summary to `report.md` without changing CSV trade records.

- [ ] **Step 3: Document report ordering**

Document that new reports place Trading Summary before Funnel and keep detailed trades in `trades.csv`.

- [ ] **Step 4: Assert the final artifact**

Verify the report has Trading Summary before Funnel, trade count 2, net PnL 3,602.55, total return 1.80%, win rate 100.00%, and final equity 203,602.55.
