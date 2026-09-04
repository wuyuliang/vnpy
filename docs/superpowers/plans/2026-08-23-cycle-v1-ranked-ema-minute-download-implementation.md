# Cycle V1 Ranked EMA Minute Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the frozen cycle_v1 minute updater to download the stable union of explicit symbols, ranking Top-N symbols, and all ranking symbols causally EMA-eligible during the requested interval.

**Architecture:** Put pure ranking, EMA, exchange resolution, and union logic in a focused `download_universe.py` module. Keep network fetching and atomic persistence in `market_data_update.py`, adding only one generic Tushare contract-reference method to `FuturesDownloader`. Preserve the RB/CU no-argument default and the existing date window.

**Tech Stack:** Python 3.13, pandas, argparse, Tushare, pytest, Ruff.

---

### Task 1: Pure Download Universe Selection

**Files:**
- Create: `cta/strategy/brooks/cycle_v1/backtest/download_universe.py`
- Create: `cta/strategy/brooks/cycle_v1/tests/test_download_universe.py`

- [ ] **Step 1: Write failing tests for ranking and EMA selection**

Test `load_ranked_symbols()` sorting/normalization and `select_ranked_universe()` stable union. Test EMA with daily closes where the request-day close would pass but shifted prior-day EMA does not.

```python
selection = select_ranked_universe(
    explicit=["LC"], top_n=2, include_ema_eligible=True,
    ranking_csv=ranking, day_root=day_root,
    start=date(2026, 1, 5), end=date(2026, 1, 7),
    contract_reference=reference,
)
assert [item.root_symbol for item in selection.selected] == ["LC", "RB", "CU", "AL"]
assert selection.selected[0].exchange == "GFEX"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_download_universe.py -q`

Expected: collection failure because `download_universe` does not exist.

- [ ] **Step 3: Implement minimal pure selection module**

Define immutable `RankedSymbol`, `SelectedSymbol`, `SelectionRejection`, and `UniverseSelection`. Implement:

```python
def load_ranked_symbols(path: str | Path) -> tuple[RankedSymbol, ...]: ...
def ema_eligible_in_interval(path: Path, start: date, end: date) -> tuple[bool, str]: ...
def select_ranked_universe(*, explicit: Sequence[str], top_n: int,
    include_ema_eligible: bool, ranking_csv: str | Path, day_root: str | Path,
    start: date, end: date, contract_reference: pd.DataFrame) -> UniverseSelection: ...
```

Normalize roots/exchanges, reject duplicate ranking roots and invalid ranks, resolve explicit symbols through ranking/day/reference, then build stable first-seen union with source sets.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_download_universe.py -q`

Expected: all tests pass.

### Task 2: Generic Contract Reference and Downloader Integration

**Files:**
- Modify: `cta/data_code/futures_downloader.py`
- Modify: `cta/strategy/brooks/cycle_v1/backtest/market_data_update.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py`
- Test: `cta/data_code/tests/test_contract_downloader.py`

- [ ] **Step 1: Write failing tests for contract reference and multi-exchange download**

Add tests that `fetch_contract_reference()` requests `fut_basic` for supported exchanges and returns normalized root/exchange coverage. Update the fake downloader to record `(root, exchange)` mapping calls and assert LC uses GFEX while RB uses SHFE.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest cta/data_code/tests/test_contract_downloader.py cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py -q`

Expected: failures for missing APIs and old fixed RB/CU behavior.

- [ ] **Step 3: Implement generic network and persistence path**

Add:

```python
def fetch_contract_reference(self, exchanges: Iterable[str] = COMMODITY_EXCHANGES | {"CFFEX"}) -> pd.DataFrame: ...
```

Extend `update_minute_data()` with a selected-symbol sequence, pass each real exchange to `fetch_fut_mapping`, reuse an existing recognizable directory or create `data_root/{ROOT}`, and normalize new generic files with the actual exchange. Retain CU compatibility repair and no-overwrite/atomic semantics.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m pytest cta/data_code/tests/test_contract_downloader.py cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py -q`

Expected: all tests pass.

### Task 3: CLI and Audit Contract

**Files:**
- Modify: `cta/strategy/brooks/cycle_v1/backtest/market_data_update.py`
- Modify: `cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py`

- [ ] **Step 1: Write failing parser and audit tests**

Assert parsing of `--symbols LC --top-n 20 --include-ema-eligible --ranking-csv ... --day-root ...`; assert no selection flags keep RB/CU; assert audit includes explicit/top_n/ema_eligible/selected and selection rejections.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py -q`

Expected: parser/audit assertions fail.

- [ ] **Step 3: Wire universe selection into CLI**

Build the contract reference only when an unresolved explicit root needs it. Pass selection into `update_minute_data`; serialize selection and rejection dataclasses into the audit summary. Catch per-symbol mapping/download errors, record them, and continue the batch without fabricating data.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py -q`

Expected: all tests pass.

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `cta/strategy/brooks/docs/20260822_cycle_v1_cli_design.md`
- Modify: `cta/strategy/brooks/docs/20260814_brooks.md`

- [ ] **Step 1: Document the production CLI and audit behavior**

Add the recommended command, causal prior-day EMA selection, no-argument compatibility, supported exchanges, and the rule that missing mechanics still blocks official performance.

- [ ] **Step 2: Run focused and regression verification**

Run:

```bash
python3 -m pytest cta/strategy/brooks/cycle_v1/tests/test_download_universe.py cta/strategy/brooks/cycle_v1/tests/test_market_data_update.py cta/data_code/tests/test_contract_downloader.py -q
python3 -m pytest cta/strategy/brooks/cycle_v1/tests -q
python3 -m pytest cta/data_code/tests -q
python3 -m ruff check cta/strategy/brooks/cycle_v1 cta/data_code/futures_downloader.py
python3 -m compileall -q cta/strategy/brooks/cycle_v1 cta/data_code/futures_downloader.py
```

- [ ] **Step 3: Run a no-network CLI parser smoke check**

Parse the recommended command in a test and inspect `--help`; do not start a live full-market download during verification.

- [ ] **Step 4: Review scope and generated CLI**

Confirm no files outside the listed code/tests/docs were changed and report any pre-existing suite failures separately from focused verification.
