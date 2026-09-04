# Cycle V1 Two Percent Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change cycle_v1's base per-trade risk and hard per-trade cap to 2% while preserving cycle multipliers and all portfolio-level gates.

**Architecture:** Keep the sizing formula unchanged and modify only the production configuration. Lock the intended semantics with production-config tests, synchronize the Brooks documentation, then generate a new immutable RB/CU report and audit the resulting funnel.

**Tech Stack:** Python 3.13, pytest, YAML configuration, pandas, cycle_v1 backtest CLI.

---

### Task 1: Lock The Production Risk Budget

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_risk.py`
- Modify: `cta/strategy/brooks/config/cycle_v1.yaml`

- [ ] **Step 1: Write the failing production-config assertions**

Update `test_position_size_uses_stop_loss_cost_and_lot_step` to assert a strong-breakout budget of `4_000.0` and quantity `34`. Add this test:

```python
def test_production_tight_channel_budget_is_one_point_six_percent() -> None:
    config = load_config()
    decision = size_position(
        equity=200_000.0,
        entry=100.0,
        stop=90.0,
        contract_multiplier=10.0,
        estimated_entry_cost=5.0,
        stressed_exit_cost=10.0,
        lot_step=1,
        cycle=MarketCycle.BULL_TIGHT_CHANNEL,
        large_confidence=0.8,
        medium_confidence=0.8,
        drawdown_multiplier=1.0,
        new_order_risk_multiplier=1.0,
        config=config,
    )

    assert config.risk.risk_per_trade == pytest.approx(0.02)
    assert config.risk.max_trade_risk == pytest.approx(0.02)
    assert decision.risk_budget == pytest.approx(3_200.0)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_risk.py::test_position_size_uses_stop_loss_cost_and_lot_step cta/strategy/brooks/cycle_v1/tests/test_risk.py::test_production_tight_channel_budget_is_one_point_six_percent -q
```

Expected: both tests fail because the production configuration still uses `0.0025` and `0.0050`.

- [ ] **Step 3: Apply the minimal configuration change**

In `cta/strategy/brooks/config/cycle_v1.yaml`, set:

```yaml
risk:
  risk_per_trade: 0.0200
  max_trade_risk: 0.0200
```

Do not change `CYCLE_MULTIPLIER`, symbol/sector/portfolio limits, margin limits, stops, or setup gates.

- [ ] **Step 4: Run focused risk tests and verify GREEN**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_risk.py -q
```

Expected: all risk tests pass, including a 3,200 tight-channel budget.

### Task 2: Synchronize Strategy Documentation

**Files:**
- Modify: `cta/strategy/brooks/docs/20260814_brooks.md`
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`

- [ ] **Step 1: Update the frozen risk values**

Replace the active cycle_v1 values with:

```text
risk_per_trade = 2.00% equity
hard maximum = 2.00% equity
```

Document effective budgets for 200,000 equity: strong breakout 4,000, tight channel 3,200, broad channel 2,400, and trading range 1,600. State explicitly that portfolio gates remain unchanged and can still reject a sized order.

- [ ] **Step 2: Verify stale active values are gone**

Run:

```bash
rg -n "risk_per_trade|max_trade_risk|0\.25%|0\.50%|2\.00%|3,200" cta/strategy/brooks/docs/20260814_brooks.md cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md
```

Expected: historical v6 audit text may retain its observed 400 budget, while active configuration sections show the new 2% semantics.

### Task 3: Verify And Publish The New Backtest

**Files:**
- Create: `cta/strategy/brooks/report/cycle_v1/20260823_official_v7_risk2pct_20260101_20260727_1h_30m_5m/`
- Modify: `cta/strategy/brooks/docs/20260814_brooks.md`
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`

- [ ] **Step 1: Run complete regression checks**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests -q
python3 -m pytest cta/strategy/brooks/scalp/tests -q
python3 -m ruff check cta/strategy/brooks/cycle_v1
```

Expected: all suites and static checks pass.

- [ ] **Step 2: Run the immutable RB/CU report**

Run:

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.runner \
  --symbols RB CU \
  --start 2026-01-01 \
  --end 2026-07-27 \
  --long-tf 1hour \
  --medium-tf 30min \
  --short-tf 5min \
  --initial-equity 200000 \
  --run-id 20260823_official_v7_risk2pct_20260101_20260727_1h_30m_5m
```

Expected: status `COMPLETE`; no metadata gaps; candidates and every downstream rejection/order/fill/trade remain auditable.

- [ ] **Step 3: Audit the funnel and risk reasons**

Verify that the eight v6 candidates remain semantically unchanged by comparing symbol, contract, setup, direction, signal/active times, and geometry. Candidate IDs are expected to change because the configuration hash is part of their identity. Candidate-level rejection rows must cover every candidate not promoted to a plan, and no order may exceed symbol, sector, total-risk, or margin limits.

- [ ] **Step 4: Record the observed v7 result**

Update both Brooks documents with the v7 report path, funnel counts, candidate-level budget/loss or portfolio rejection reasons, and official net performance. Do not describe zero trades as an implementation failure when a frozen risk gate rejected the order.
