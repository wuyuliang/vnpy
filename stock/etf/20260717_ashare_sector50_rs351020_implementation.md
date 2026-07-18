# A-Share ETF Sector Cap, RS 3/5/10/20, ATR5 and ADX5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict the ETF rotation strategy to mainland A-share index ETFs, rank them with the approved 3/5/10/20 RS formula, cap classified industry exposure at 50% when orders execute, use ATR5 for risk sizing/stops and ADX5 for trend filters, then run the live 2024-05-17 through 2026-07-17 backtest and render trade charts.

**Architecture:** Keep metadata filtering and deterministic industry classification in `data.py`, factor calculations in `indicators.py`, cross-sectional scoring in `ranking.py`, quantity limits in `portfolio.py`, and execution-time industry accounting in `backtest.py`. The existing CLI and chart renderer remain orchestration layers; no new framework or external classification dependency is introduced.

**Tech Stack:** Python 3.13, pandas, NumPy, Pillow, Tushare, unittest/pytest, Ruff.

**Workspace note:** The repository contains unrelated untracked work. Edit only `stock/etf/**`, preserve existing outputs, and do not create a git commit unless the user asks.

---

### Task 1: Strict A-Share Index Universe and Industry Classification

**Files:**
- Modify: `stock/etf/data.py:94-163`
- Test: `stock/etf/tests/test_data.py`

- [ ] **Step 1: Write failing universe and industry tests**

Add tests that construct listed funds with `name`, `fund_type`, `benchmark`, and `status`, then assert:

```python
result = prepare_a_share_index_metadata(funds).set_index("symbol")
self.assertEqual(set(result.index), {"A.SH", "HS_A.SH", "SP_A.SH", "MSCI_A.SH"})
self.assertTrue((result["asset_scope"] == "a_share_index").all())
self.assertEqual(result.loc["A.SH", "industry"], "semiconductor")
self.assertNotIn("NASDAQ.SH", result.index)
self.assertNotIn("NIKKEI.SH", result.index)
self.assertNotIn("HK.SH", result.index)
self.assertNotIn("BOND.SH", result.index)
```

Add a second test:

```python
self.assertEqual(classify_etf_industry("芯片ETF", "中证芯片指数收益率×100%"), "semiconductor")
self.assertEqual(classify_etf_industry("集成电路ETF", "国证集成电路指数"), "semiconductor")
self.assertEqual(classify_etf_industry("沪深300ETF", "沪深300指数收益率×100%"), "broad_or_other")
self.assertEqual(classify_etf_industry("新能源车ETF", "中证新能源汽车指数"), "automobile")
```

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_data.py -q
```

Expected: failure because `prepare_a_share_index_metadata` and `classify_etf_industry` do not exist.

- [ ] **Step 3: Implement deterministic metadata preparation**

In `data.py`, add ordered industry rules and strict scope helpers with these public signatures:

```python
import re


INDUSTRY_RULES: tuple[tuple[str, str], ...] = (
    ("semiconductor", r"半导体|芯片|集成电路"),
    ("electronics", r"消费电子|光学光电子|元器件|电子"),
    ("computer_ai", r"人工智能|计算机|软件|云计算|大数据|信创|数字经济"),
    ("communication", r"通信|5G"),
    ("machinery", r"工业母机|工程机械|高端装备|机床|机器人|机械"),
    ("automobile", r"新能源汽车|新能源车|智能车|汽车"),
    ("new_energy", r"新能源|光伏|电池|锂电|储能|风电"),
    ("finance", r"银行|证券|保险|金融"),
    ("healthcare", r"医药|医疗|生物科技|创新药|中药"),
    ("consumer", r"食品饮料|家电|旅游|消费|白酒|酒"),
    ("materials", r"稀有金属|有色|钢铁|化工|建材"),
    ("defense", r"航空航天|军工|国防"),
    ("utilities", r"绿色电力|公用事业|电力"),
    ("energy", r"石油|油气|煤炭|传统能源|能源"),
    ("agriculture", r"农业|畜牧|养殖|粮食"),
    ("real_estate", r"房地产|地产"),
    ("construction", r"工程建设|建筑|基建"),
    ("transportation", r"交通运输|物流|航运"),
    ("media", r"传媒|游戏|影视"),
    ("environmental", r"环保"),
)

FOREIGN_MARKET_PATTERN = (
    r"QDII|港股|香港|沪港深|沪深港|粤港澳|中概|海外|全球|跨境|境外|"
    r"纳斯达克|NASDAQ|"
    r"日经|NIKKEI|道琼斯|DOWJONES|美国|日本|德国|法国|印度|越南|"
    r"新加坡|韩国|沙特|东南亚|亚太|DAX|恒生|标普|S&P"
)
DOMESTIC_A_SHARE_PATTERN = (
    r"A股|中国A50|沪深|中证|上证|深证|国证|创业板|科创板|科创|"
    r"北证|全指|巨潮|央视"
)


def _metadata_text(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def classify_etf_industry(name: object, benchmark: object) -> str:
    text = f"{_metadata_text(name)}|{_metadata_text(benchmark)}"
    for industry, pattern in INDUSTRY_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return industry
    return "broad_or_other"


def prepare_a_share_index_metadata(funds: pd.DataFrame) -> pd.DataFrame:
    frame = filter_etf_universe(funds)
    benchmark = frame.get("benchmark", pd.Series("", index=frame.index)).fillna("").astype(str)
    name = frame.get("name", pd.Series("", index=frame.index)).fillna("").astype(str)
    text = (name + "|" + benchmark).str.replace(r"\s+", "", regex=True)
    protected = text.str.replace("恒生A股", "A股", regex=False)
    protected = protected.str.replace("标普中国A股", "A股", regex=False)
    protected = protected.str.replace("S&P中国A股", "A股", regex=False)
    foreign = protected.str.contains(FOREIGN_MARKET_PATTERN, case=False, regex=True)
    stock = frame["fund_type"].fillna("").astype(str).eq("股票型ETF")
    index_benchmark = benchmark.str.contains("指数", regex=False) & ~benchmark.str.contains(
        r"\+|＋", regex=True
    )
    domestic = text.str.contains(DOMESTIC_A_SHARE_PATTERN, case=False, regex=True)
    frame = frame.loc[stock & index_benchmark & domestic & ~foreign].copy()
    frame["asset_scope"] = "a_share_index"
    frame["industry"] = [
        classify_etf_industry(row.get("name"), row.get("benchmark"))
        for _, row in frame.iterrows()
    ]
    return frame.reset_index(drop=True)
```

The explicit replacements protect domestic provider exceptions before applying the foreign pattern; `MSCI中国A股`, `MSCI中国A50`, and `富时中国A50` pass via their domestic evidence and do not match the foreign pattern. Make `TushareEtfDownloader.fetch_metadata()` return prepared metadata so excluded instruments are not downloaded.

- [ ] **Step 4: Run data tests and verify green**

Run:

```bash
python3 -m pytest stock/etf/tests/test_data.py -q
```

Expected: all data tests pass.

### Task 2: RS 3/5/10/20, EMA5 Slope, and Normalized ATR5

**Files:**
- Modify: `stock/etf/indicators.py:47-67`
- Modify: `stock/etf/ranking.py:8-151`
- Modify: `stock/etf/backtest.py:38-52`
- Test: `stock/etf/tests/test_indicators.py`
- Test: `stock/etf/tests/test_ranking.py`

- [ ] **Step 1: Change indicator tests to the approved formulas**

At row 99 assert:

```python
expected_ema5 = close.ewm(span=5, adjust=False, min_periods=5).mean()
self.assertAlmostEqual(result.loc[99, "return_3"], 100 / 97 - 1)
self.assertAlmostEqual(result.loc[99, "return_5"], 100 / 95 - 1)
self.assertAlmostEqual(result.loc[99, "return_10"], 100 / 90 - 1)
self.assertAlmostEqual(result.loc[99, "return_20"], 100 / 80 - 1)
self.assertAlmostEqual(
    result.loc[99, "ema5_slope"],
    expected_ema5.loc[99] / expected_ema5.loc[94] - 1,
)
self.assertAlmostEqual(
    result.loc[99, "normalized_atr5"],
    result.loc[99, "atr5"] / result.loc[99, "close"],
)
```

Update ranking fixtures to provide `return_3`, `return_5`, `return_10`, `return_20`, `ema5_slope`, and `normalized_atr5`. Add a direct score assertion using the approved weights and a tie test where only `return_5` differs.

- [ ] **Step 2: Run indicator/ranking tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_indicators.py stock/etf/tests/test_ranking.py -q
```

Expected: failures for missing new factor columns and old RS weights.

- [ ] **Step 3: Implement the factor columns and score**

In `add_indicators` calculate:

```python
for period in (3, 5, 10, 20):
    result[f"return_{period}"] = close / close.shift(period) - 1
result["ema5_slope"] = result["ema5"] / result["ema5"].shift(5) - 1
result["atr5"] = calculate_atr(result, atr_period)
result["normalized_atr5"] = result["atr5"] / close
result["adx5"] = calculate_adx(result, adx_period)
```

Set ranking columns to:

```python
RANK_COLUMNS = (
    "return_3",
    "return_5",
    "return_10",
    "return_20",
    "ema5_slope",
    "normalized_atr5",
)
```

Calculate:

```python
frame["rs_score"] = (
    0.35 * frame["rank_return_3"]
    + 0.35 * frame["rank_return_5"]
    + 0.20 * frame["rank_return_10"]
    + 0.10 * frame["rank_return_20"]
    + 0.20 * frame["rank_ema5_slope"]
    - 0.20 * frame["rank_normalized_atr5"]
)
```

Sort ties by `return_5`, then turnover and symbol. Update candidate output columns to the new audit names; use `atr5` internally for position sizing/stops and `adx5` for both trend filters.

- [ ] **Step 4: Run indicator/ranking tests and verify green**

Run the same focused pytest command. Expected: all tests pass.

### Task 3: Configurable Industry Notional Limit

**Files:**
- Modify: `stock/etf/config.py:5-34`
- Modify: `stock/etf/portfolio.py:49-71`
- Test: `stock/etf/tests/test_portfolio.py`

- [ ] **Step 1: Write failing quantity-cap tests**

Add:

```python
quantity = calculate_order_quantity(
    equity=100_000,
    available_cash=100_000,
    estimated_fill_price=10,
    atr5=0.01,
    config=StrategyConfig(commission_rate=0, min_commission=0),
    max_additional_notional=10_000,
)
self.assertEqual(quantity, 1000)

blocked = calculate_order_quantity(
    equity=100_000,
    available_cash=100_000,
    estimated_fill_price=10,
    atr5=0.01,
    config=StrategyConfig(commission_rate=0, min_commission=0),
    max_additional_notional=999,
)
self.assertEqual(blocked, 0)
```

Also assert `StrategyConfig(max_industry_weight=0)` and values above 1 raise `ValueError`.

- [ ] **Step 2: Run portfolio tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_portfolio.py -q
```

Expected: failures for the missing config field and function argument.

- [ ] **Step 3: Add the minimal reusable limit**

Add `max_industry_weight: float = 0.50` to `StrategyConfig` and validate it is in `(0, 1]`.
Extend the quantity function signature:

```python
def calculate_order_quantity(
    equity: float,
    available_cash: float,
    estimated_fill_price: float,
    atr5: float,
    config: StrategyConfig,
    *,
    max_additional_notional: float | None = None,
) -> int:
```

When the optional limit is supplied, add:

```python
notional_units = floor(max(max_additional_notional, 0.0) / estimated_fill_price)
raw_units = min(raw_units, notional_units)
```

- [ ] **Step 4: Run portfolio tests and verify green**

Run the focused portfolio tests. Expected: all pass.

### Task 4: Backtest Industry Exposure and Audit Fields

**Files:**
- Modify: `stock/etf/backtest.py:29-305`
- Test: `stock/etf/tests/test_backtest.py`
- Test: `stock/etf/tests/test_cli.py`

- [ ] **Step 1: Write failing end-to-end sector-cap tests**

Build four rising synthetic ETFs whose metadata all map to `semiconductor`, set fees and slippage to zero and `risk_per_trade=1`, then assert:

```python
filled = result.signals.loc[result.signals["action"] == "buy_filled"]
classified = filled.loc[filled["industry"] == "semiconductor"]
self.assertFalse(classified.empty)
self.assertLessEqual(classified["industry_weight_after_entry"].max(), 0.500001)
self.assertIn("industry_cap", result.signals["reason"].values)
self.assertIn("industry", result.positions.columns)
self.assertIn("industry_weight", result.positions.columns)
```

Add a separate synthetic run with broad-index metadata and assert three `broad_or_other` entries are not combined under the 50% cap. Add valid `benchmark` fields to all existing backtest and CLI metadata fixtures because strict scope filtering fails closed.

- [ ] **Step 2: Run backtest and CLI tests and verify red**

Run:

```bash
python3 -m pytest stock/etf/tests/test_backtest.py stock/etf/tests/test_cli.py -q
```

Expected: failures for missing metadata preparation, industry fields, and execution cap.

- [ ] **Step 3: Filter metadata at the backtest boundary**

In `_prepare_etfs`, call `prepare_a_share_index_metadata(metadata)`, restrict daily bars to selected symbols, merge `benchmark`, `asset_scope`, and `industry`, and raise a clear `ValueError` if no eligible A-share index ETF has bars.

- [ ] **Step 4: Track execution-time industry exposure**

Add a focused helper:

```python
def _industry_market_values(
    portfolio: Portfolio,
    industry_by_symbol: dict[str, str],
    daily: pd.DataFrame,
    last_close: dict[str, float],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for symbol, position in portfolio.positions.items():
        industry = industry_by_symbol.get(symbol, "broad_or_other")
        if industry == "broad_or_other":
            continue
        if symbol in daily.index and pd.notna(daily.loc[symbol, "open"]):
            price = float(daily.loc[symbol, "open"])
        else:
            price = last_close.get(symbol, position.average_price)
        values[industry] = values.get(industry, 0.0) + position.quantity * price
    return values
```

Carry industry in each pending entry. Before each buy, calculate capacity from `previous_equity`, pass it as `max_additional_notional`, update exposure using actual fill notional, and write `industry_weight_after_entry`. If the uncapped quantity is positive but the capped quantity is zero, append:

```python
{
    "signal_date": signal_date,
    "execution_date": date,
    "symbol": symbol,
    "action": "buy_skipped",
    "reason": "industry_cap",
    "industry": industry,
    "industry_weight_after_entry": current_exposure / previous_equity,
}
```

- [ ] **Step 5: Add close-time position industry audit**

Compute close market values for every position, aggregate classified industries, and add `industry` and `industry_weight` to every position record. Use a missing value for `broad_or_other` aggregate weight because that label is explicitly exempt and not a shared industry bucket.

- [ ] **Step 6: Run backtest and CLI tests and verify green**

Run the same focused pytest command. Expected: all tests pass.

### Task 5: Documentation, Schemas, and Full Offline Review

**Files:**
- Modify: `stock/etf/etf_rotation_strategy.md`
- Modify: `stock/etf/20260717_ashare_sector50_rs351020_design.md` only if implementation exposes a contradiction
- Review: `stock/etf/render_trade_charts.py`
- Test: `stock/etf/tests/**`

- [ ] **Step 1: Update the canonical strategy document**

Replace multi-asset universe language and obsolete RS fields with the approved A-share-only scope and 3/5/10/20 formula. Add `max_industry_weight=0.50`, the execution-only cap, and output audit fields. Specify ATR5 risk sizing/stops and ADX5 market/ETF trend filters, with both periods fixed at 5.

- [ ] **Step 2: Run all ETF tests**

Run:

```bash
python3 -m pytest stock/etf/tests -q
```

Expected: every test passes.

- [ ] **Step 3: Run static checks**

Run:

```bash
python3 -m ruff check stock/etf
python3 -m compileall -q stock/etf
```

Expected: Ruff reports `All checks passed!`; compileall exits zero.

- [ ] **Step 4: Self-review the changed logic**

Review every changed line for future-data use, scope-filter false positives, industry priority collisions, cap calculations, cash safety, stable output ordering, and compatibility with the chart renderer. Add a regression test before fixing any issue found.

### Task 6: Cached Real Tushare 26-Month ATR5/ADX5 Backtest

**Files:**
- Read: `stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/**`
- Create: `stock/etf/output/20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live/**`

- [ ] **Step 1: Reuse the verified real cache and run the ATR5/ADX5 backtest**

Run:

```bash
python3 -m stock.etf.run_etf_rotation \
  --start 2024-05-17 \
  --end 2026-07-17 \
  --run-id 20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live \
  --skip-download \
  --benchmark-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/benchmark.csv \
  --etf-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/etfs.csv \
  --metadata-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/metadata.csv \
  --output-dir stock/etf/output/20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live
```

Expected: the verified metadata, benchmark and ETF cache is read without network access; all six backtest outputs are written; process exits zero. The cache remains unchanged so the old and new strategy results are directly comparable.

- [ ] **Step 2: Validate the real universe and portfolio constraints**

Read metadata, trades, signals, positions, equity, and summary. Assert:

```text
asset_scope is exactly a_share_index
fund_type is exactly 股票型ETF
no traded name or benchmark matches the overseas exclusion rules
all buy quantities are multiples of 100
all non-null buy_filled industry_weight_after_entry values are <= 0.500001
cash is never negative
all required outputs are non-empty or have valid header-only schemas
```

Report actual dates, metadata count, trade count, buy/sell count, final equity, return, drawdown, and any remaining open positions.

### Task 7: Render and Verify Final Trade Charts

**Files:**
- Create: `stock/etf/output/20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live/charts/**`

- [ ] **Step 1: Render one chart per traded ETF**

Run:

```bash
python3 -m stock.etf.render_trade_charts \
  --daily-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/etfs.csv \
  --metadata-csv stock/etf/data/20260717_last26months_ashare_sector50_rs351020_live/metadata.csv \
  --trades-csv stock/etf/output/20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live/trades.csv \
  --positions-csv stock/etf/output/20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live/positions.csv \
  --output-dir stock/etf/output/20260717_last26months_ashare_sector50_rs351020_atr5_adx5_live/charts \
  --report-start 2024-05-17 \
  --report-end 2026-07-17 \
  --overwrite
```

Expected: no missing names or daily data and one PNG for each unique traded symbol.

- [ ] **Step 2: Verify chart artifacts**

Check that `index.csv` rows and summed `trade_count` match `trades.csv`, every indexed PNG exists, Pillow can decode every PNG, every image is 1680 x 1000, and every image contains at least one blue or orange trade marker. Visually inspect the highest-PnL, lowest-PnL, busiest, and open-position examples for readable labels and unclipped Chinese names.

- [ ] **Step 3: Final regression verification**

Run:

```bash
python3 -m pytest stock/tests stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m compileall -q stock/etf
```

Expected: all tests pass, Ruff passes, compileall exits zero, and the final response links the charts directory, index, summary, strategy code, and design/implementation documents.
