# Remove Mean Reversion VOI Taper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely remove the three opt-in modules added by `mean_reversion_voi_regime_adaptive_design.md` without reverting unrelated CTA work.

**Architecture:** Keep the existing baseline, generic feature, and OOT runtime pipelines intact while removing only the module-specific branches for `mean_reversion_range`, oscillation taper, and VOI regime-adaptive momentum. Add narrow regression checks first so the rollback is guarded by public API and CLI behavior rather than broad file deletion.

**Tech Stack:** Python 3.10+, pandas, unittest/pytest, vn.py CTA local modules.

---

### Task 1: Lock Rollback Boundaries With Tests

**Files:**
- Modify: `cta/strategy/tests/test_mean_reversion_range_setup.py`
- Modify: `cta/feature/tests/test_voi_regime_adaptive_momentum.py`
- Modify: `cta/model/tests/test_group_pool_mode.py`

- [ ] **Step 1: Replace module behavior assertions with rollback assertions**

```python
def test_baseline_signal_types_do_not_publish_mean_reversion_range(self) -> None:
    self.assertNotIn("mean_reversion_range", BASELINE_SIGNAL_TYPES)
```

```python
def test_feature_batch_cli_does_not_publish_voi_opt_in_flag(self) -> None:
    parser = build_parser()
    self.assertNotIn("--voi-enabled-cells", parser.format_help())
```

```python
def test_model_cli_does_not_publish_oscillation_taper_flag(self) -> None:
    help_text = _capture_model_help()
    self.assertNotIn("--enable-oscillation-taper", help_text)
```

- [ ] **Step 2: Run the focused tests and confirm they fail against current code**

Run:

```bash
python3 -m pytest -q \
  cta/strategy/tests/test_mean_reversion_range_setup.py \
  cta/feature/tests/test_voi_regime_adaptive_momentum.py \
  cta/model/tests/test_group_pool_mode.py
```

Expected: FAIL while the three module-specific APIs are still wired.

### Task 2: Remove Module Wiring

**Files:**
- Delete: `cta/config/mean_reversion_setup_config.py`
- Delete: `cta/feature/mean_reversion.py`
- Delete: `cta/strategy/mean_reversion_range_setup.py`
- Delete: `cta/config/voi_momentum_config.py`
- Delete: `cta/feature/voi_momentum.py`
- Delete: `cta/portfolio_logic/oscillation_taper.py`
- Modify: baseline candidate/setup/strategy files under `cta/strategy/`
- Modify: feature compute and batch dispatch files under `cta/feature/`
- Modify: portfolio runtime and OOT files under `cta/portfolio_logic/` and `cta/model/`

- [ ] **Step 1: Remove mean-reversion setup from baseline signal enumeration and candidate generation**
- [ ] **Step 2: Remove VOI cfg/feature injection from single-symbol and batch generic feature entrypoints**
- [ ] **Step 3: Remove oscillation taper config, trailing evaluator path, OOT detail columns, and model CLI flag**
- [ ] **Step 4: Run focused tests until rollback checks pass**

### Task 3: Clean Documentation And Verify

**Files:**
- Modify: `cta/run.md`
- Modify: `cta/model/model.md`
- Modify: `cta/feature/FEATURES.md`
- Modify: `cta/strategy/readme.md`
- Modify: `cta/portfolio_logic/README.md`
- Modify: `cta/keyword.md`
- Modify: `cta/docs/block_reason.md`
- Modify: `cta/report/change_log.md`

- [ ] **Step 1: Remove runtime guidance and keyword entries for the deleted modules**
- [ ] **Step 2: Record the rollback in `cta/report/change_log.md`**
- [ ] **Step 3: Run focused tests, docs sync tests, and compilation**

Run:

```bash
python3 -m pytest -q cta/strategy/tests cta/feature/tests cta/model/tests/test_group_pool_mode.py cta/run/tests/test_docs_sync.py
python3 -m compileall -q cta/config cta/feature cta/model cta/portfolio_logic cta/strategy
git diff --check -- cta
```

Expected: exit code 0 for the selected rollback verification commands.
