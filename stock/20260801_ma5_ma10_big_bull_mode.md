# MA5/MA10 大牛股模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `stock/` 主线中新增第四种 `signal_type=ma5_ma10_big_bull`，用 MA5 高于 MA10 作为触发入口，但通过趋势、相对强度、量能、涨停基因、过热约束和每日 TopN 冷却机制，只保留更像“大牛股起涨/主升”的少量候选，并增加“不提前下车”的大牛股卖出研究。

**Architecture:** 保持现有 `stock/` 三层结构：本地日线 CSV 输入，`strategy/` 负责信号与评分，`run/` 串联下载、扫描、过滤、日报和 analysis，`analysis/` 负责 PNG 图表。新增逻辑尽量独立在 `stock/strategy/big_bull_mode.py` 和 `stock/backtest/big_bull_exit_research.py`，只在 `sample_pipeline.py` 做最小集成，避免继续放大已有 `signal_evaluators.py`。

**Tech Stack:** Python 3.10+、pandas、Pillow、pytest、Tushare 本地日线 CSV。

**Implementation reconciliation (updated 2026-08-02):** 用户最终指定的专用日报根目录为 `stock/report/opportunities_date_ma5_ma10_big/`，覆盖本文初稿中的旧名称。`rs_60d_pct` 使用机会日全部非 ST 股票的横截面，评分 API 强制传入该市场分布；先剔除冷却 symbol 再取每日 TopN；大牛股机会的 `entry_datetime/entry_price` 使用下一交易日及其 open。每日目录仍按机会日列出候选 symbol，但 PNG 已按用户最新要求改为截至运行 `end` 的 symbol 回看汇总图，包含该 symbol 全部买点、两月时间线及彩色虚线箭头，因此不能作为 point-in-time 快照。近两年涨跌停统计不读取回测 `end` 后数据；退出研究只匹配精确机会日，不把停牌缺失日顺延入场，setup low 不含入场日且从入场日开始检查止损，不可交易行不绘制虚假 marker；Chandelier Exit 使用持仓期最高价 high 减 `3 x ATR20`，与 `future_mfe_pct` 的最高价口径一致。普通 analysis 保持 buy-only，只有独立退出研究图通过显式开关绘制卖点。

---

## Scope

新增主线信号：

- `ma5_ma10_big_bull`

保留已有三种信号：

- `bull_pullback_continuation`
- `breakout_pullback_continuation`
- `volume_spike_up`

这次实现仍然只做研究链路：

- 下载或复用日线数据
- 扫描买入候选
- 输出 opportunity CSV
- 输出每日机会 PNG 目录
- 输出 symbol 级 analysis PNG
- 输出卖出规则研究结果和买卖点图

这次仍然不做真实下单、仓位管理、资金曲线组合回测、分钟级数据。

## Signal Definition

### 1. 基础触发

`ma5_ma10_big_bull` 不是简单地每天输出 `MA5 > MA10`，否则数量会失控。只在“状态切换”时输出：

- `ma5 >= ma10`
- 前一交易日 `ma5 < ma10`，即本次必须是从下向上的状态切换
- `close >= ma10`
- `close >= 3.0`
- 当前信号日不能是本地日线最后一行，因为 `opportunity_date` 必须是下一交易日

候选评分后再按 `opportunity_date` 顺序执行 symbol 冷却；没有发生新上穿时不会仅因冷却期结束而重复发信号。

均线使用简单移动平均线：

- `ma5 = close.rolling(5).mean()`
- `ma10 = close.rolling(10).mean()`
- `ma20 = close.rolling(20).mean()`
- `ma60 = close.rolling(60).mean()`
- `ma120 = close.rolling(120).mean()`

图表继续保留现有 EMA5、EMA10、EMA20；新增信号会把 MA5、MA10、MA20、MA60、MA120 的数值写入 opportunity CSV，用于标题、筛选复核和 chart 复核。

### 2. 硬过滤

只有基础触发还太宽，因此增加硬过滤：

- 非 ST：沿用现有 `filter_non_st_symbols()` 和 `filter_non_st_opportunities()`
- 趋势向上：`ma20_slope_5d > 0`
- 中期不弱：`close >= ma60` 或 `ma60_slope_20d > 0`
- 不追太高：`close_to_ma20 <= 1.25`
- 不追短线暴涨：`return_20d <= 0.80`
- 量能确认：`volume_ratio_20 >= 1.3` 或 `volume_3_avg / volume_20_avg >= 1.2`
- 涨停/活跃基因：`limit_up_count_120d >= 1` 或 `big_volume_up_count_60d >= 2`
- 相对强度：同一 `opportunity_date` 全部非 ST 股票的 `return_60d` 分位数 `rs_60d_pct >= 0.70`

字段定义：

- `ma20_slope_5d = ma20 / ma20.shift(5) - 1`
- `ma60_slope_20d = ma60 / ma60.shift(20) - 1`
- `return_20d = close / close.shift(20) - 1`
- `return_60d = close / close.shift(60) - 1`
- `return_120d = close / close.shift(120) - 1`
- `volume_20_avg = volume.shift(1).rolling(20).mean()`
- `volume_ratio_20 = volume / volume_20_avg`
- `volume_3_avg = volume.rolling(3).mean()`
- `volume_3_to_20 = volume_3_avg / volume_20_avg`
- `limit_up_count_120d`：主板用 `pct_chg >= 9.8`，`300/301/688/689` 板块用 `pct_chg >= 19.8`，再做 120 日滚动计数
- `big_volume_up_count_60d = ((pct_chg > 3.0) & (volume_ratio_20 >= 1.8)).rolling(60).sum()`
- `close_to_ma20 = close / ma20`

### 3. Scoring

用 `big_bull_score` 控制候选数量，满分 100：

- 趋势结构 25 分：`ma5 >= ma10 >= ma20`、`ma20_slope_5d > 0`、`close >= ma60`
- 相对强度 25 分：`rs_60d_pct`、`return_120d`
- 量能 15 分：`volume_ratio_20`、`volume_3_to_20`
- 平台/突破 15 分：`close_to_120d_high`、`base_compression_60d`
- 涨停/活跃 10 分：`limit_up_count_120d`、`big_volume_up_count_60d`
- 不过热 10 分：`close_to_ma20 <= 1.25`、`return_20d <= 0.80`

默认保留规则：

- `big_bull_score >= 70`
- 每个 `opportunity_date` 最多保留 `30` 只
- 每个 symbol 在 `20` 个自然日内最多出现一次

排序规则：

- `opportunity_date` 倒序用于日报目录
- 同一天内部按 `big_bull_score` 降序
- 分数相同按 `rs_60d_pct` 降序
- 再相同按 `volume_ratio_20` 降序
- 再相同按 `symbol` 升序

## Output Paths

新增输出：

- 机会表：`stock/report/opportunities/{run_id}_stock_signal_opportunities.csv`
- 大牛股每日目录：`stock/report/opportunities_date_ma5_ma10_big/{run_id}/0001_{YYYY-MM-DD}/*.png`
- symbol 级图表：`stock/analysis/{run_id}/charts/*.png`
- 卖出研究表：`stock/report/exit_research/{run_id}_big_bull_exit_trades.csv`
- 卖出研究摘要：`stock/report/exit_research/{run_id}_big_bull_exit_summary.json`
- 买卖点图：`stock/analysis/{exit_run_id}/charts/*.png`

新增 opportunity 字段：

- `ma5`
- `ma10`
- `ma20`
- `ma60`
- `ma120`
- `ma20_slope_5d`
- `ma60_slope_20d`
- `return_20d`
- `return_60d`
- `return_120d`
- `rs_60d_pct`
- `volume_20_avg`
- `volume_ratio_20`
- `volume_3_to_20`
- `limit_up_count_120d`
- `big_volume_up_count_60d`
- `close_to_ma20`
- `close_to_120d_high`
- `base_compression_60d`
- `big_bull_score`
- `candidate_reason`

## File Structure

Create:

- `stock/strategy/big_bull_mode.py`：计算 MA5/MA10 大牛股候选、特征、横截面 RS、评分、TopN 和冷却过滤。
- `stock/backtest/__init__.py`：卖出研究包入口。
- `stock/backtest/big_bull_exit_research.py`：读取机会表和本地 K 线，研究大牛股模式退出与对照退出。
- `stock/tests/test_big_bull_mode.py`：信号和评分单测。
- `stock/tests/test_big_bull_exit_research.py`：卖出研究单测。

Modify:

- `stock/run/sample_pipeline.py`：新增 `signal_type`、调用大牛股扫描、横截面过滤、专用每日目录、CLI 参数。
- `stock/analysis/render_symbol_bull_pullback_charts.py`：新增信号颜色和标签，买点图能识别 `ma5_ma10_big_bull`。
- `stock/tests/test_sample_pipeline.py`：覆盖 CLI、pipeline、专用日报目录。
- `stock/tests/test_render_symbol_bull_pullback_charts.py`：覆盖新 signal label/color。
- `stock/AGENTS.md`：把第四种信号和输出目录写入开发约束。
- `stock/stock.md`：记录第四种信号、运行命令和交付物。

---

## Task 1: Add Big Bull Strategy Tests

**Files:**

- Create: `stock/tests/test_big_bull_mode.py`

- [x] **Step 1: Add the test file**

```python
from __future__ import annotations

import pandas as pd

from stock.strategy.big_bull_mode import (
    BIG_BULL_SIGNAL_TYPE,
    BigBullConfig,
    score_and_filter_big_bull_opportunities,
    scan_ma5_ma10_big_bull,
)


def _frame(symbol: str = "000001.SZ") -> pd.DataFrame:
    closes = [
        10.0, 10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.3, 9.2,
        9.4, 9.7, 10.0, 10.3, 10.7, 11.1, 11.5, 11.9, 12.3, 12.7,
        13.0, 13.4, 13.8, 14.2, 14.6, 15.0, 15.5, 15.9, 16.3, 16.8,
    ]
    dates = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    volumes = [1000.0] * 20 + [1600.0, 1800.0, 1900.0, 2100.0, 2300.0, 2400.0, 2500.0, 2600.0, 2700.0, 2800.0]
    pct_chg = pd.Series(closes).pct_change().fillna(0.0) * 100.0
    return pd.DataFrame(
        {
            "symbol": symbol,
            "exchange": "SZSE" if symbol.endswith(".SZ") else "SSE",
            "interval": "d",
            "datetime": dates.strftime("%Y-%m-%d 00:00:00"),
            "open": [value * 0.99 for value in closes],
            "high": [value * 1.03 for value in closes],
            "low": [value * 0.97 for value in closes],
            "close": closes,
            "pct_chg": pct_chg,
            "volume": volumes,
            "open_interest": 0.0,
            "turnover": [volume * close for volume, close in zip(volumes, closes)],
        }
    )


def test_scan_ma5_ma10_big_bull_emits_next_day_opportunity() -> None:
    cfg = BigBullConfig(
        min_history_bars=10,
        min_close=1.0,
        min_score=0.0,
        min_rs_60d_pct=0.0,
        max_candidates_per_day=30,
        cooldown_days=0,
        min_turnover_20_avg=0.0,
        max_close_to_ma20=2.0,
        max_return_20d=9.0,
        min_limit_up_count_120d=0,
        min_big_volume_up_count_60d=0,
    )

    result = scan_ma5_ma10_big_bull(_frame(), cfg)

    assert not result.empty
    assert set(result["signal_type"]) == {BIG_BULL_SIGNAL_TYPE}
    assert set(result["entry_action"]) == {"buy"}
    assert result["opportunity_date"].iloc[0] > result["signal_datetime"].str.slice(0, 10).iloc[0]
    assert {"ma5", "ma10", "ma20", "volume_ratio_20", "return_20d", "candidate_reason"}.issubset(result.columns)


def test_score_and_filter_big_bull_opportunities_keeps_daily_topn_and_cooldown() -> None:
    raw = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-01",
                "return_60d": 0.20,
                "return_120d": 0.30,
                "volume_ratio_20": 1.4,
                "volume_3_to_20": 1.2,
                "ma5": 11.0,
                "ma10": 10.8,
                "ma20": 10.0,
                "ma20_slope_5d": 0.02,
                "ma60_slope_20d": 0.01,
                "close_price": 11.0,
                "ma60": 10.5,
                "close_to_ma20": 1.10,
                "close_to_120d_high": 0.96,
                "base_compression_60d": 0.25,
                "limit_up_count_120d": 1,
                "big_volume_up_count_60d": 2,
            },
            {
                "symbol": "000002.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-01",
                "return_60d": 0.50,
                "return_120d": 0.70,
                "volume_ratio_20": 2.0,
                "volume_3_to_20": 1.8,
                "ma5": 12.0,
                "ma10": 11.4,
                "ma20": 10.5,
                "ma20_slope_5d": 0.04,
                "ma60_slope_20d": 0.03,
                "close_price": 12.0,
                "ma60": 10.8,
                "close_to_ma20": 1.14,
                "close_to_120d_high": 0.99,
                "base_compression_60d": 0.18,
                "limit_up_count_120d": 2,
                "big_volume_up_count_60d": 4,
            },
            {
                "symbol": "000003.SZ",
                "signal_type": BIG_BULL_SIGNAL_TYPE,
                "opportunity_date": "2024-03-01",
                "return_60d": 0.35,
                "return_120d": 0.40,
                "volume_ratio_20": 1.6,
                "volume_3_to_20": 1.4,
                "ma5": 11.5,
                "ma10": 11.1,
                "ma20": 10.4,
                "ma20_slope_5d": 0.03,
                "ma60_slope_20d": 0.02,
                "close_price": 11.5,
                "ma60": 10.7,
                "close_to_ma20": 1.10,
                "close_to_120d_high": 0.98,
                "base_compression_60d": 0.20,
                "limit_up_count_120d": 1,
                "big_volume_up_count_60d": 3,
            },
        ]
    )
    cfg = BigBullConfig(min_score=0.0, min_rs_60d_pct=0.0, max_candidates_per_day=2, cooldown_days=20)

    result = score_and_filter_big_bull_opportunities(raw, cfg)

    assert list(result["symbol"]) == ["000002.SZ", "000003.SZ"]
    assert result["big_bull_score"].is_monotonic_decreasing
    assert result["rs_60d_pct"].between(0.0, 1.0).all()


def test_score_and_filter_keeps_non_big_bull_rows_unchanged() -> None:
    raw = pd.DataFrame(
        [
            {"symbol": "600000.SH", "signal_type": "volume_spike_up", "opportunity_date": "2024-03-01"},
            {"symbol": "000001.SZ", "signal_type": BIG_BULL_SIGNAL_TYPE, "opportunity_date": "2024-03-01", "return_60d": 0.2},
        ]
    )
    cfg = BigBullConfig(min_score=100.0, min_rs_60d_pct=1.0, max_candidates_per_day=1, cooldown_days=20)

    result = score_and_filter_big_bull_opportunities(raw, cfg)

    assert "600000.SH" in set(result["symbol"])
    assert BIG_BULL_SIGNAL_TYPE not in set(result["signal_type"])
```

- [x] **Step 2: Run the failing test**

Run:

```bash
python3 -m pytest stock/tests/test_big_bull_mode.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'stock.strategy.big_bull_mode'
```

## Task 2: Implement Big Bull Strategy Module

**Files:**

- Create: `stock/strategy/big_bull_mode.py`

- [x] **Step 1: Add constants, config, and feature preparation**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


BIG_BULL_SIGNAL_TYPE = "ma5_ma10_big_bull"
BIG_BULL_TRIGGER = "ma5_cross_ma10_big_bull_long"

BIG_BULL_COLUMNS: list[str] = [
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "ma20_slope_5d",
    "ma60_slope_20d",
    "return_20d",
    "return_60d",
    "return_120d",
    "rs_60d_pct",
    "volume_20_avg",
    "volume_ratio_20",
    "volume_3_to_20",
    "limit_up_count_120d",
    "big_volume_up_count_60d",
    "close_to_ma20",
    "close_to_120d_high",
    "base_compression_60d",
    "big_bull_score",
    "candidate_reason",
]


@dataclass(frozen=True)
class BigBullConfig:
    min_history_bars: int = 120
    ma_fast: int = 5
    ma_mid: int = 10
    ma_slow: int = 20
    ma_trend: int = 60
    ma_long: int = 120
    cooldown_days: int = 20
    min_close: float = 3.0
    min_turnover_20_avg: float = 100000.0
    min_volume_ratio_20: float = 1.3
    min_volume_3_to_20: float = 1.2
    max_close_to_ma20: float = 1.25
    max_return_20d: float = 0.80
    min_rs_60d_pct: float = 0.70
    min_limit_up_count_120d: int = 1
    min_big_volume_up_count_60d: int = 2
    min_score: float = 70.0
    max_candidates_per_day: int = 30


def _empty_big_bull_frame() -> pd.DataFrame:
    base_columns = [
        "symbol",
        "exchange",
        "signal_type",
        "signal_datetime",
        "opportunity_date",
        "signal_price",
        "close_price",
        "entry_datetime",
        "entry_price",
        "entry_action",
        "trigger",
    ]
    return pd.DataFrame(columns=base_columns + BIG_BULL_COLUMNS)


def prepare_big_bull_features(frame: pd.DataFrame, cfg: BigBullConfig) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    bars = frame.copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "turnover", "pct_chg"]:
        if column in bars.columns:
            bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars["volume"] = bars.get("volume", pd.Series(index=bars.index, dtype="float64")).fillna(0.0)
    bars["turnover"] = bars.get("turnover", pd.Series(index=bars.index, dtype="float64")).fillna(0.0)
    bars["pct_chg"] = bars.get("pct_chg", pd.Series(index=bars.index, dtype="float64"))
    bars = bars.dropna(subset=["datetime", "close", "high", "low"]).sort_values("datetime").reset_index(drop=True)

    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    volume = pd.to_numeric(bars["volume"], errors="coerce").fillna(0.0)
    turnover = pd.to_numeric(bars["turnover"], errors="coerce").fillna(0.0)
    pct_chg = pd.to_numeric(bars["pct_chg"], errors="coerce").fillna(close.pct_change() * 100.0)

    for window, column in [
        (cfg.ma_fast, "ma5"),
        (cfg.ma_mid, "ma10"),
        (cfg.ma_slow, "ma20"),
        (cfg.ma_trend, "ma60"),
        (cfg.ma_long, "ma120"),
    ]:
        bars[column] = close.rolling(window).mean()

    bars["ma20_slope_5d"] = bars["ma20"] / bars["ma20"].shift(5) - 1.0
    bars["ma60_slope_20d"] = bars["ma60"] / bars["ma60"].shift(20) - 1.0
    bars["return_20d"] = close / close.shift(20) - 1.0
    bars["return_60d"] = close / close.shift(60) - 1.0
    bars["return_120d"] = close / close.shift(120) - 1.0
    bars["volume_20_avg"] = volume.shift(1).rolling(20).mean()
    bars["volume_3_avg"] = volume.rolling(3).mean()
    bars["volume_ratio_20"] = volume / bars["volume_20_avg"]
    bars["volume_3_to_20"] = bars["volume_3_avg"] / bars["volume_20_avg"]
    bars["turnover_20_avg"] = turnover.shift(1).rolling(20).mean()
    bars["limit_up_count_120d"] = (pct_chg >= 9.8).rolling(120, min_periods=1).sum()
    bars["big_volume_up_count_60d"] = ((pct_chg > 3.0) & (bars["volume_ratio_20"] >= 1.8)).rolling(60, min_periods=1).sum()
    bars["high_120d"] = high.rolling(120, min_periods=1).max()
    bars["low_60d"] = low.rolling(60, min_periods=1).min()
    bars["high_60d"] = high.rolling(60, min_periods=1).max()
    bars["close_to_ma20"] = close / bars["ma20"]
    bars["close_to_120d_high"] = close / bars["high_120d"]
    bars["base_compression_60d"] = bars["high_60d"] / bars["low_60d"] - 1.0
    return bars
```

- [x] **Step 2: Add scanner and reason formatter**

```python
def _reason(row: pd.Series) -> str:
    parts = [
        f"ma5>=ma10 {row['ma5']:.2f}>={row['ma10']:.2f}",
        f"rs60={row.get('rs_60d_pct', 0.0):.2f}",
        f"vol20={row.get('volume_ratio_20', 0.0):.2f}",
        f"limit120={int(row.get('limit_up_count_120d', 0))}",
    ]
    return "; ".join(parts)


def scan_ma5_ma10_big_bull(frame: pd.DataFrame, cfg: BigBullConfig | None = None) -> pd.DataFrame:
    cfg = cfg or BigBullConfig()
    bars = prepare_big_bull_features(frame, cfg)
    if bars.empty or len(bars) < max(cfg.min_history_bars, cfg.ma_mid) + 1:
        return _empty_big_bull_frame()

    close = pd.to_numeric(bars["close"], errors="coerce")
    ma5 = pd.to_numeric(bars["ma5"], errors="coerce")
    ma10 = pd.to_numeric(bars["ma10"], errors="coerce")
    ma20 = pd.to_numeric(bars["ma20"], errors="coerce")
    ma60 = pd.to_numeric(bars["ma60"], errors="coerce")
    volume_ratio_20 = pd.to_numeric(bars["volume_ratio_20"], errors="coerce")
    volume_3_to_20 = pd.to_numeric(bars["volume_3_to_20"], errors="coerce")
    turnover_20_avg = pd.to_numeric(bars["turnover_20_avg"], errors="coerce")

    ma_cross = (ma5 >= ma10) & (ma5.shift(1) < ma10.shift(1))
    enough_history = pd.Series(range(len(bars)), index=bars.index) >= cfg.min_history_bars - 1
    trend_ok = (pd.to_numeric(bars["ma20_slope_5d"], errors="coerce") > 0) & (
        (close >= ma60) | (pd.to_numeric(bars["ma60_slope_20d"], errors="coerce") > 0)
    )
    volume_ok = (volume_ratio_20 >= cfg.min_volume_ratio_20) | (volume_3_to_20 >= cfg.min_volume_3_to_20)
    active_ok = (
        pd.to_numeric(bars["limit_up_count_120d"], errors="coerce").fillna(0) >= cfg.min_limit_up_count_120d
    ) | (
        pd.to_numeric(bars["big_volume_up_count_60d"], errors="coerce").fillna(0) >= cfg.min_big_volume_up_count_60d
    )
    liquidity_ok = turnover_20_avg.fillna(0.0) >= cfg.min_turnover_20_avg
    not_overheated = (pd.to_numeric(bars["close_to_ma20"], errors="coerce") <= cfg.max_close_to_ma20) & (
        pd.to_numeric(bars["return_20d"], errors="coerce") <= cfg.max_return_20d
    )

    signal_mask = (
        enough_history
        & ma_cross
        & (close >= ma10)
        & (close >= cfg.min_close)
        & trend_ok
        & volume_ok
        & active_ok
        & liquidity_ok
        & not_overheated
    )
    if len(signal_mask) > 0:
        signal_mask.iloc[-1] = False

    rows: list[dict[str, Any]] = []
    for signal_index in bars.index[signal_mask.fillna(False)]:
        opportunity_index = int(signal_index) + 1
        signal_row = bars.iloc[signal_index]
        opportunity_row = bars.iloc[opportunity_index]
        signal_datetime = pd.Timestamp(signal_row["datetime"]).strftime("%Y-%m-%d %H:%M:%S")
        row: dict[str, Any] = {
            "symbol": str(signal_row["symbol"]),
            "exchange": str(signal_row["exchange"]),
            "signal_type": BIG_BULL_SIGNAL_TYPE,
            "signal_datetime": signal_datetime,
            "opportunity_date": pd.Timestamp(opportunity_row["datetime"]).strftime("%Y-%m-%d"),
            "signal_price": float(signal_row["close"]),
            "close_price": float(signal_row["close"]),
            "entry_datetime": signal_datetime,
            "entry_price": float(signal_row["close"]),
            "entry_action": "buy",
            "trigger": BIG_BULL_TRIGGER,
        }
        for column in BIG_BULL_COLUMNS:
            if column in signal_row.index:
                row[column] = signal_row[column]
        row["rs_60d_pct"] = pd.NA
        row["big_bull_score"] = pd.NA
        row["candidate_reason"] = _reason(pd.Series(row))
        rows.append(row)

    if not rows:
        return _empty_big_bull_frame()
    return pd.DataFrame(rows).loc[:, _empty_big_bull_frame().columns]
```

- [x] **Step 3: Add cross-sectional scoring and selection**

```python
def _clip_score(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if pd.isna(value):
        return 0.0
    return max(low, min(high, float(value)))


def _calculate_big_bull_score(frame: pd.DataFrame) -> pd.Series:
    data = frame.copy()
    trend = (
        ((data["ma5"] >= data["ma10"]) & (data["ma10"] >= data["ma20"])).astype(float) * 10.0
        + (data["ma20_slope_5d"] > 0).astype(float) * 7.5
        + (data["close_price"] >= data["ma60"]).astype(float) * 7.5
    )
    rs = data["rs_60d_pct"].fillna(0.0).clip(0.0, 1.0) * 20.0 + (data["return_120d"].fillna(0.0).clip(0.0, 1.0) * 5.0)
    volume = data["volume_ratio_20"].fillna(0.0).clip(0.0, 3.0) / 3.0 * 8.0 + data["volume_3_to_20"].fillna(0.0).clip(0.0, 2.5) / 2.5 * 7.0
    structure = data["close_to_120d_high"].fillna(0.0).clip(0.0, 1.0) * 8.0 + (1.0 - data["base_compression_60d"].fillna(1.0).clip(0.0, 1.0)) * 7.0
    active = data["limit_up_count_120d"].fillna(0.0).clip(0.0, 3.0) / 3.0 * 5.0 + data["big_volume_up_count_60d"].fillna(0.0).clip(0.0, 5.0) / 5.0 * 5.0
    not_hot = (data["close_to_ma20"] <= 1.25).astype(float) * 5.0 + (data["return_20d"] <= 0.80).astype(float) * 5.0
    return (trend + rs + volume + structure + active + not_hot).round(2)


def _apply_symbol_cooldown(frame: pd.DataFrame, cooldown_days: int) -> pd.DataFrame:
    if frame.empty or cooldown_days <= 0:
        return frame.reset_index(drop=True)
    selected: list[pd.Series] = []
    last_date_by_symbol: dict[str, pd.Timestamp] = {}
    ordered = frame.sort_values(["opportunity_date", "big_bull_score", "rs_60d_pct"], ascending=[True, False, False])
    for _, row in ordered.iterrows():
        symbol = str(row["symbol"])
        current_date = pd.Timestamp(row["opportunity_date"])
        last_date = last_date_by_symbol.get(symbol)
        if last_date is not None and (current_date - last_date).days < cooldown_days:
            continue
        selected.append(row)
        last_date_by_symbol[symbol] = current_date
    if not selected:
        return frame.iloc[0:0].copy()
    return pd.DataFrame(selected).reset_index(drop=True)


def score_and_filter_big_bull_opportunities(merged: pd.DataFrame, cfg: BigBullConfig | None = None) -> pd.DataFrame:
    cfg = cfg or BigBullConfig()
    if merged.empty or "signal_type" not in merged.columns:
        return merged

    other = merged[merged["signal_type"].astype(str) != BIG_BULL_SIGNAL_TYPE].copy()
    big = merged[merged["signal_type"].astype(str) == BIG_BULL_SIGNAL_TYPE].copy()
    if big.empty:
        return merged.reset_index(drop=True)

    numeric_columns = [
        "ma5", "ma10", "ma20", "ma60", "return_60d", "return_120d",
        "volume_ratio_20", "volume_3_to_20", "ma20_slope_5d", "close_price",
        "close_to_ma20", "close_to_120d_high", "base_compression_60d",
        "limit_up_count_120d", "big_volume_up_count_60d", "return_20d",
    ]
    for column in numeric_columns:
        if column not in big.columns:
            big[column] = pd.NA
        big[column] = pd.to_numeric(big[column], errors="coerce")

    big["rs_60d_pct"] = big.groupby("opportunity_date")["return_60d"].rank(pct=True, method="average")
    big["big_bull_score"] = _calculate_big_bull_score(big)
    big = big[(big["rs_60d_pct"] >= cfg.min_rs_60d_pct) & (big["big_bull_score"] >= cfg.min_score)].copy()
    if not big.empty:
        big = (
            big.sort_values(["opportunity_date", "big_bull_score", "rs_60d_pct", "volume_ratio_20", "symbol"], ascending=[True, False, False, False, True])
            .groupby("opportunity_date", group_keys=False)
            .head(cfg.max_candidates_per_day)
            .reset_index(drop=True)
        )
        big = _apply_symbol_cooldown(big, cfg.cooldown_days)

    combined = pd.concat([other, big], ignore_index=True, sort=False)
    if "opportunity_date" in combined.columns:
        return combined.sort_values(["opportunity_date", "symbol", "signal_type"]).reset_index(drop=True)
    return combined.reset_index(drop=True)
```

- [x] **Step 4: Run strategy tests**

Run:

```bash
python3 -m pytest stock/tests/test_big_bull_mode.py -q
```

Expected:

```text
3 passed
```

## Task 3: Integrate Big Bull Into Pipeline

**Files:**

- Modify: `stock/run/sample_pipeline.py`
- Modify: `stock/tests/test_sample_pipeline.py`

- [x] **Step 1: Add imports and default daily root**

Add near the existing strategy imports:

```python
from stock.strategy.big_bull_mode import (
    BIG_BULL_COLUMNS,
    BIG_BULL_SIGNAL_TYPE,
    BigBullConfig,
    scan_ma5_ma10_big_bull,
    score_and_filter_big_bull_opportunities,
)
```

Add near `_default_breakout_daily_root()`:

```python
def _default_big_bull_daily_root() -> Path:
    return Path(__file__).resolve().parents[1] / "report" / "opportunities_date_ma5_ma10_big"
```

- [x] **Step 2: Extend empty opportunity schema**

In `_empty_opportunity_frame()`, append `BIG_BULL_COLUMNS` after the existing current columns, preserving current columns first:

```python
base_columns = [
    "symbol",
    "exchange",
    "name",
    "total_mv",
    "circ_mv",
    "limit_up_count_2y",
    "limit_down_count_2y",
    "signal_type",
    "signal_datetime",
    "opportunity_date",
    "signal_price",
    "close_price",
    "entry_datetime",
    "entry_price",
    "entry_action",
    "trigger",
    "ema5",
    "ema10",
    "ema20",
    "volume",
    "volume_prev",
    "volume_5_avg",
    "volume_3_avg",
    "volume_10_avg",
    "volume_condition",
    "lookback_high_18m",
    "close_to_lookback_high",
    "breakout_level",
    "pullback_low",
    "pullback_high",
    "bars_since_breakout",
]
return pd.DataFrame(columns=base_columns + [column for column in BIG_BULL_COLUMNS if column not in base_columns])
```

- [x] **Step 3: Extend scan dispatch**

Change `scan_frame_for_signal_type()` to accept `big_bull_cfg` and include the fourth signal:

```python
def scan_frame_for_signal_type(
    frame: pd.DataFrame,
    cfg: BullPullbackConfig,
    signal_type: str,
    *,
    big_bull_cfg: BigBullConfig | None = None,
) -> pd.DataFrame:
    if signal_type == "all":
        rows = [
            scan_stock_signal_opportunities(frame, cfg),
            scan_ma5_ma10_big_bull(frame, big_bull_cfg or BigBullConfig()),
        ]
        non_empty = [item for item in rows if not item.empty]
        return pd.concat(non_empty, ignore_index=True, sort=False) if non_empty else _empty_opportunity_frame()
    if signal_type == BIG_BULL_SIGNAL_TYPE:
        return scan_ma5_ma10_big_bull(frame, big_bull_cfg or BigBullConfig())
    if signal_type == "volume_spike_up":
        return scan_volume_spike_up(frame, cfg)
    if signal_type == "bull_pullback_continuation":
        return scan_bull_pullback_continuation(frame, cfg)
    if signal_type == "breakout_pullback_continuation":
        return scan_breakout_pullback_continuation(frame, cfg)
    raise ValueError(f"unsupported signal_type: {signal_type}")
```

- [x] **Step 4: Score/filter after merge and before enrichment**

In `run_pipeline()`, add parameter:

```python
big_bull_cfg: BigBullConfig | None = None,
```

Set default after `cfg = cfg or BullPullbackConfig()`:

```python
big_bull_cfg = big_bull_cfg or BigBullConfig()
```

Pass it into scan:

```python
scanned = scan_frame_for_signal_type(frame, cfg, signal_type, big_bull_cfg=big_bull_cfg)
```

After date filtering and before enrichment:

```python
merged = score_and_filter_big_bull_opportunities(merged, big_bull_cfg)
```

- [x] **Step 5: Add specialized daily writer**

Add near `write_breakout_pullback_daily_dirs()`:

```python
def write_big_bull_daily_dirs(merged: pd.DataFrame, daily_root: Path, run_id: str, *, data_root: Path) -> Path:
    if merged.empty or "signal_type" not in merged.columns:
        big_bull_frame = merged.iloc[0:0].copy()
    else:
        big_bull_frame = merged[merged["signal_type"].astype(str) == BIG_BULL_SIGNAL_TYPE].reset_index(drop=True)
    return write_daily_opportunity_dirs(big_bull_frame, daily_root, run_id, data_root=data_root)
```

Add parameter to `run_pipeline()`:

```python
big_bull_daily_root: Path | None = None,
```

Resolve default:

```python
big_bull_daily_root = Path(big_bull_daily_root) if big_bull_daily_root is not None else _default_big_bull_daily_root()
```

Write directory and include in result:

```python
big_bull_daily_opportunity_dir = write_big_bull_daily_dirs(merged, big_bull_daily_root, run_id, data_root=data_root)
```

```python
"big_bull_daily_opportunity_dir": str(big_bull_daily_opportunity_dir),
```

- [x] **Step 6: Extend CLI choices**

In `build_arg_parser()`, change `--signal-type` choices:

```python
choices=[
    "all",
    "bull_pullback_continuation",
    "breakout_pullback_continuation",
    "volume_spike_up",
    BIG_BULL_SIGNAL_TYPE,
],
```

- [x] **Step 7: Add pipeline tests**

Append to `stock/tests/test_sample_pipeline.py`:

```python
def test_arg_parser_accepts_big_bull_signal_type() -> None:
    args = build_arg_parser().parse_args(["--universe", "all", "--signal-type", "ma5_ma10_big_bull", "--merge-existing"])

    assert args.signal_type == "ma5_ma10_big_bull"


def test_write_big_bull_daily_dirs_filters_other_signal_types(tmp_path: Path) -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "exchange": "SZSE",
                "name": "平安银行",
                "signal_type": "ma5_ma10_big_bull",
                "signal_datetime": "2024-02-01 00:00:00",
                "opportunity_date": "2024-02-02",
                "entry_price": 10.0,
                "signal_price": 10.0,
                "close_price": 10.0,
                "entry_action": "buy",
                "trigger": "ma5_cross_ma10_big_bull_long",
            },
            {
                "symbol": "000002.SZ",
                "exchange": "SZSE",
                "name": "万科A",
                "signal_type": "volume_spike_up",
                "signal_datetime": "2024-02-01 00:00:00",
                "opportunity_date": "2024-02-02",
                "entry_price": 9.0,
                "signal_price": 9.0,
                "close_price": 9.0,
                "entry_action": "buy",
                "trigger": "volume_spike_up_long",
            },
        ]
    )
    day_dir = tmp_path / "data" / "day"
    day_dir.mkdir(parents=True)
    _write_day_csv(day_dir / "000001_SZ.csv", symbol="000001.SZ")

    output_root = write_big_bull_daily_dirs(
        rows,
        tmp_path / "report" / "opportunities_date_ma5_ma10_big",
        "run1",
        data_root=tmp_path / "data",
    )

    png_names = [path.name for path in output_root.glob("**/*.png")]
    assert png_names
    assert all("ma5_ma10_big_bull" in name for name in png_names)
```

- [x] **Step 8: Run pipeline tests**

Run:

```bash
python3 -m pytest stock/tests/test_sample_pipeline.py -q
```

Expected:

```text
all tests in stock/tests/test_sample_pipeline.py pass
```

## Task 4: Update Chart Labels

**Files:**

- Modify: `stock/analysis/render_symbol_bull_pullback_charts.py`
- Modify: `stock/tests/test_render_symbol_bull_pullback_charts.py`

- [x] **Step 1: Add color and label**

Add to `SIGNAL_TYPE_COLORS`:

```python
"ma5_ma10_big_bull": "#16a34a",
```

Add to `SIGNAL_TYPE_LABELS`:

```python
"ma5_ma10_big_bull": "MA5/MA10 Big Bull",
```

- [x] **Step 2: Add renderer test row**

In the multi-signal renderer test, add one row:

```python
{
    "symbol": "000001.SZ",
    "exchange": "SZSE",
    "name": "平安银行",
    "signal_type": "ma5_ma10_big_bull",
    "signal_datetime": "2024-02-05 00:00:00",
    "opportunity_date": "2024-02-06",
    "entry_price": 11.2,
    "signal_price": 11.2,
    "close_price": 11.2,
    "entry_action": "buy",
    "trigger": "ma5_cross_ma10_big_bull_long",
}
```

Assert:

```python
assert "ma5_ma10_big_bull" in (output_dir / "index.csv").read_text()
```

- [x] **Step 3: Run chart tests**

Run:

```bash
python3 -m pytest stock/tests/test_render_symbol_bull_pullback_charts.py -q
```

Expected:

```text
all tests in stock/tests/test_render_symbol_bull_pullback_charts.py pass
```

## Task 5: Add Big Bull Exit Research Tests

**Files:**

- Create: `stock/backtest/__init__.py`
- Create: `stock/tests/test_big_bull_exit_research.py`

- [x] **Step 1: Add package file**

Create `stock/backtest/__init__.py`:

```python
"""Research-only stock backtest helpers."""
```

- [x] **Step 2: Add exit research tests**

```python
from __future__ import annotations

import pandas as pd

from stock.backtest.big_bull_exit_research import BigBullExitConfig, simulate_big_bull_exit


def _bars() -> pd.DataFrame:
    closes = [
        10, 10.5, 11, 12, 13, 14, 16, 18, 21, 24,
        28, 31, 35, 38, 42, 45, 49, 53, 58, 62,
        61, 60, 59, 57, 55, 53, 51, 49, 47, 45,
    ]
    dates = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "datetime": dates.strftime("%Y-%m-%d 00:00:00"),
            "open": [value * 0.99 for value in closes],
            "high": [value * 1.03 for value in closes],
            "low": [value * 0.97 for value in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )


def test_simulate_big_bull_exit_holds_through_main_rise() -> None:
    result = simulate_big_bull_exit(
        _bars(),
        entry_date="2024-01-03",
        cfg=BigBullExitConfig(initial_stop_pct=0.10, trend_profit_activate_pct=0.20),
    )

    assert result["entry_price"] > 0
    assert result["exit_price"] > result["entry_price"] * 3.0
    assert result["holding_days"] >= 20
    assert result["exit_rule"] in {"big_bull_ma20_break", "big_bull_chandelier"}
    assert result["capture_ratio"] >= 0.70


def test_simulate_big_bull_exit_uses_initial_stop_before_trend_mode() -> None:
    bars = _bars().iloc[:8].copy()
    bars.loc[3:, "close"] = [9.8, 9.4, 9.0, 8.8, 8.6]

    result = simulate_big_bull_exit(
        bars,
        entry_date="2024-01-03",
        cfg=BigBullExitConfig(initial_stop_pct=0.10, trend_profit_activate_pct=0.20),
    )

    assert result["exit_rule"] == "initial_stop"
    assert result["return_pct"] < 0
```

- [x] **Step 3: Run the failing tests**

Run:

```bash
python3 -m pytest stock/tests/test_big_bull_exit_research.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'stock.backtest.big_bull_exit_research'
```

## Task 6: Implement Big Bull Exit Research

**Files:**

- Create: `stock/backtest/big_bull_exit_research.py`

- [x] **Step 1: Add config and indicator preparation**

```python
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class BigBullExitConfig:
    initial_stop_pct: float = 0.10
    trend_profit_activate_pct: float = 0.20
    ma20_break_days: int = 2
    atr_window: int = 20
    chandelier_atr_multiple: float = 3.0
    max_holding_days: int = 240


def prepare_exit_bars(frame: pd.DataFrame) -> pd.DataFrame:
    bars = frame.copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    bars["ma5"] = close.rolling(5).mean()
    bars["ma10"] = close.rolling(10).mean()
    bars["ma20"] = close.rolling(20, min_periods=5).mean()
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    bars["atr20"] = tr.rolling(20, min_periods=5).mean()
    return bars
```

- [x] **Step 2: Add main 大牛股模式 exit simulation**

```python
def simulate_big_bull_exit(
    frame: pd.DataFrame,
    *,
    entry_date: str,
    cfg: BigBullExitConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or BigBullExitConfig()
    bars = prepare_exit_bars(frame)
    if bars.empty:
        return {
            "entry_date": entry_date,
            "entry_price": pd.NA,
            "exit_date": "",
            "exit_price": pd.NA,
            "exit_rule": "missing_bars",
            "holding_days": 0,
            "return_pct": pd.NA,
            "mfe_pct": pd.NA,
            "mae_pct": pd.NA,
            "capture_ratio": pd.NA,
        }

    parsed_entry = pd.to_datetime(entry_date, errors="coerce")
    if pd.isna(parsed_entry):
        return _empty_exit_result(entry_date, "invalid_entry_date")
    entry_ts = pd.Timestamp(parsed_entry).normalize()
    entry_candidates = bars[bars["datetime"].dt.normalize() == entry_ts]
    if entry_candidates.empty:
        rule = "entry_after_data" if entry_ts > bars["datetime"].dt.normalize().max() else "missing_entry_bar"
        return _empty_exit_result(entry_date, rule)

    entry_index = int(entry_candidates.index[0])
    entry_row = bars.iloc[entry_index]
    entry_price = float(entry_row["open"]) if pd.notna(entry_row["open"]) else float(entry_row["close"])
    setup_window = bars.iloc[max(0, entry_index - 5): entry_index]
    setup_stop = float(setup_window["low"].min()) * 0.97 if not setup_window.empty else float("-inf")
    initial_stop = max(setup_stop, entry_price * (1.0 - cfg.initial_stop_pct))
    highest_high = float(entry_row["high"])
    trend_mode = False
    below_ma20_days = 0

    last_holding_index = min(len(bars) - 1, entry_index + cfg.max_holding_days)
    exit_index = last_holding_index
    exit_rule = "max_holding_days" if last_holding_index == entry_index + cfg.max_holding_days else "end_of_data"
    exit_fill_price = None

    if float(entry_row["low"]) <= initial_stop:
        exit_index = entry_index
        exit_rule = "initial_stop"
        exit_fill_price = min(entry_price, initial_stop)

    remaining_indices = [] if exit_fill_price is not None else range(entry_index + 1, last_holding_index + 1)
    for idx in remaining_indices:
        row = bars.iloc[idx]
        close = float(row["close"])
        if not trend_mode and float(row["low"]) <= initial_stop:
            exit_index = idx
            exit_rule = "initial_stop"
            exit_fill_price = min(float(row["open"]), initial_stop) if pd.notna(row["open"]) else initial_stop
            break
        highest_high = max(highest_high, float(row["high"]))
        profit_pct = close / entry_price - 1.0

        if not trend_mode and profit_pct >= cfg.trend_profit_activate_pct:
            trend_mode = True

        ma20 = float(row["ma20"]) if pd.notna(row["ma20"]) else close
        if trend_mode:
            below_ma20_days = below_ma20_days + 1 if close < ma20 else 0
            ma5 = float(row["ma5"]) if pd.notna(row["ma5"]) else close
            ma10 = float(row["ma10"]) if pd.notna(row["ma10"]) else close
            atr20 = float(row["atr20"]) if pd.notna(row["atr20"]) and float(row["atr20"]) > 0 else 0.0
            chandelier = highest_high - cfg.chandelier_atr_multiple * atr20
            if below_ma20_days >= cfg.ma20_break_days and ma5 < ma10:
                exit_index = idx
                exit_rule = "big_bull_ma20_break"
                break
            chandelier_active = highest_high / entry_price - 1.0 >= 0.30
            if chandelier_active and atr20 > 0 and close < chandelier:
                exit_index = idx
                exit_rule = "big_bull_chandelier"
                break

    exit_row = bars.iloc[exit_index]
    exit_price = exit_fill_price if exit_fill_price is not None else float(exit_row["close"])
    holding_days = int(exit_index - entry_index)
    return_pct = exit_price / entry_price - 1.0
    window = bars.iloc[entry_index : exit_index + 1]
    mfe_pct = float(window["high"].max()) / entry_price - 1.0
    mae_pct = float(window["low"].min()) / entry_price - 1.0
    future_window = bars.iloc[entry_index : last_holding_index + 1]
    future_mfe_pct = float(future_window["high"].max()) / entry_price - 1.0
    capture_ratio = return_pct / future_mfe_pct if future_mfe_pct > 0 else 0.0
    return {
        "entry_date": pd.Timestamp(entry_row["datetime"]).strftime("%Y-%m-%d"),
        "entry_price": entry_price,
        "exit_date": pd.Timestamp(exit_row["datetime"]).strftime("%Y-%m-%d"),
        "exit_price": exit_price,
        "exit_rule": exit_rule,
        "holding_days": holding_days,
        "return_pct": return_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "future_mfe_pct": future_mfe_pct,
        "capture_ratio": capture_ratio,
    }
```

- [x] **Step 3: Add CLI wrapper**

```python
def load_symbol_bars(data_root: Path, symbol: str) -> pd.DataFrame:
    path = Path(data_root) / "day" / f"{symbol.replace('.', '_')}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def run_exit_research(*, opportunity_csv: Path, data_root: Path, output_dir: Path, run_id: str, cfg: BigBullExitConfig | None = None) -> dict[str, Any]:
    opportunities = pd.read_csv(opportunity_csv)
    opportunities = opportunities[opportunities["signal_type"].astype(str) == "ma5_ma10_big_bull"].copy()
    rows: list[dict[str, Any]] = []
    for opportunity in opportunities.to_dict("records"):
        symbol = str(opportunity["symbol"])
        bars = load_symbol_bars(data_root, symbol)
        result = simulate_big_bull_exit(bars, entry_date=str(opportunity["opportunity_date"]), cfg=cfg)
        result.update({"symbol": symbol, "name": opportunity.get("name", ""), "signal_datetime": opportunity.get("signal_datetime", "")})
        rows.append(result)

    output_dir.mkdir(parents=True, exist_ok=True)
    trades_csv = output_dir / f"{run_id}_big_bull_exit_trades.csv"
    trades = pd.DataFrame(rows)
    trades.to_csv(trades_csv, index=False, encoding="utf-8-sig")
    summary = {
        "run_id": run_id,
        "rows": len(trades),
        "trades_csv": str(trades_csv),
        "avg_return_pct": float(pd.to_numeric(trades.get("return_pct"), errors="coerce").mean()) if not trades.empty else 0.0,
        "avg_capture_ratio": float(pd.to_numeric(trades.get("capture_ratio"), errors="coerce").mean()) if not trades.empty else 0.0,
    }
    summary_path = output_dir / f"{run_id}_big_bull_exit_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run big bull exit research.")
    parser.add_argument("--opportunity-csv", required=True)
    parser.add_argument("--data-root", default=str(Path(__file__).resolve().parents[1] / "data" / "origin"))
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parents[1] / "report" / "exit_research"))
    parser.add_argument("--run-id", required=True)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    result = run_exit_research(
        opportunity_csv=Path(args.opportunity_csv),
        data_root=Path(args.data_root),
        output_dir=Path(args.output_dir),
        run_id=str(args.run_id),
    )
    print(result)


if __name__ == "__main__":
    main()
```

- [x] **Step 4: Run exit research tests**

Run:

```bash
python3 -m pytest stock/tests/test_big_bull_exit_research.py -q
```

Expected:

```text
all tests in stock/tests/test_big_bull_exit_research.py pass
```

## Task 7: Documentation Updates

**Files:**

- Modify: `stock/AGENTS.md`
- Modify: `stock/stock.md`

- [x] **Step 1: Update `stock/AGENTS.md` current range**

Add `ma5_ma10_big_bull` to the current signal list:

```markdown
- `ma5_ma10_big_bull`
```

Add signal description:

```markdown
- `ma5_ma10_big_bull`：以 `MA5 >= MA10` 的状态切换作为买点触发，再用趋势、相对强度、量能、涨停/活跃基因、不过热约束和每日 TopN 冷却机制，只保留更像大牛股起涨/主升的候选
```

Add output path:

```markdown
`ma5_ma10_big_bull` 专用每日目录输出到 `stock/report/opportunities_date_ma5_ma10_big/{run_id}/`。
```

- [x] **Step 2: Update `stock/stock.md`**

Add a new section:

````markdown
## 2026-08-01 第四种主线：MA5/MA10 大牛股模式

- [x] 增加 `ma5_ma10_big_bull`
- [x] 候选触发不是每天 `MA5 > MA10`，而是 `MA5` 上穿或重新站上 `MA10` 的状态切换
- [x] 增加横截面 `return_60d` 相对强度分位数 `rs_60d_pct`
- [x] 增加 `big_bull_score`，默认保留 `>=70` 分
- [x] 每个交易日最多保留 30 只
- [x] 每个 symbol 20 天内最多出现一次
- [x] 专用日报目录：`stock/report/opportunities_date_ma5_ma10_big/{run_id}/`
- [x] 卖出研究使用 `stock/backtest/big_bull_exit_research.py`

运行命令：

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type ma5_ma10_big_bull --start 2024-08-01 --end 2026-08-01 --merge-existing --run-id 20260801_ma5_ma10_big_bull
```

卖出研究命令：

```bash
python3 -m stock.backtest.big_bull_exit_research --opportunity-csv stock/report/opportunities/20260801_ma5_ma10_big_bull_stock_signal_opportunities.csv --data-root stock/data/origin --run-id 20260801_ma5_ma10_big_bull_exit
```
````

## Task 8: Full Verification

**Files:**

- No code changes in this task.

- [x] **Step 1: Run focused tests**

Run:

```bash
python3 -m pytest stock/tests/test_big_bull_mode.py stock/tests/test_big_bull_exit_research.py stock/tests/test_sample_pipeline.py stock/tests/test_render_symbol_bull_pullback_charts.py -q
```

Expected:

```text
all selected tests pass
```

- [x] **Step 2: Run all stock tests**

Run:

```bash
python3 -m pytest stock/tests -q
```

Expected:

```text
all stock tests pass
```

- [x] **Step 3: Run compile check**

Run:

```bash
python3 -m compileall -q stock
```

Expected:

```text
command exits with code 0 and no output
```

- [x] **Step 4: Run sample pipeline**

Run:

```bash
python3 -m stock.run.sample_pipeline --universe sample --signal-type ma5_ma10_big_bull --start 2024-08-01 --end 2026-08-01 --merge-existing --run-id 20260801_sample_big_bull
```

Expected result keys:

```text
run_id='20260801_sample_big_bull'
opportunity_csv='stock/report/opportunities/20260801_sample_big_bull_stock_signal_opportunities.csv'
big_bull_daily_opportunity_dir='stock/report/opportunities_date_ma5_ma10_big/20260801_sample_big_bull'
analysis_dir='stock/analysis/20260801_sample_big_bull'
```

- [x] **Step 5: Run full market pipeline**

Run:

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type ma5_ma10_big_bull --start 2024-08-01 --end 2026-08-01 --merge-existing --run-id 20260801_ma5_ma10_big_bull
```

Expected checks:

```bash
test -f stock/report/opportunities/20260801_ma5_ma10_big_bull_stock_signal_opportunities.csv
test -d stock/report/opportunities_date_ma5_ma10_big/20260801_ma5_ma10_big_bull
test -d stock/analysis/20260801_ma5_ma10_big_bull/charts
```

- [x] **Step 6: Run exit research**

Run:

```bash
python3 -m stock.backtest.big_bull_exit_research --opportunity-csv stock/report/opportunities/20260801_ma5_ma10_big_bull_stock_signal_opportunities.csv --data-root stock/data/origin --run-id 20260801_ma5_ma10_big_bull_exit
```

Expected checks:

```bash
test -f stock/report/exit_research/20260801_ma5_ma10_big_bull_exit_big_bull_exit_trades.csv
test -f stock/report/exit_research/20260801_ma5_ma10_big_bull_exit_big_bull_exit_summary.json
```

## Code Review Checklist

- `ma5_ma10_big_bull` 不应该污染已有三种信号的筛选结果。
- `signal_type="all"` 应包含四种信号；单独指定任意一种信号时只输出该类型。
- `score_and_filter_big_bull_opportunities()` 只过滤 `ma5_ma10_big_bull` 行，其他 signal rows 原样保留。
- 每日目录按 `0001_YYYY-MM-DD` 倒序，最新日期排最上面。
- 专用目录 `stock/report/opportunities_date_ma5_ma10_big/{run_id}/` 只包含 `ma5_ma10_big_bull` PNG。
- `filter_non_st_symbols()` 和 `filter_non_st_opportunities()` 仍然覆盖全流程。
- 股票名称为空时 fail closed，不允许离线 fallback 绕过 ST 过滤。
- `rs_60d_pct` 使用机会日全市场非 ST 股票，不使用候选子集。
- 有大牛股行时必须传入 `market_returns_by_date`，缺失时显式报错，不允许候选子集回退。
- 冷却过滤先于每日 TopN，冷却 symbol 被剔除后允许后续排名补位。
- `opportunity_date` 使用下一交易日，不用信号日当天做买入执行日期。
- 每日目录只使用当天机会行决定候选 symbol；PNG 使用该 symbol 全部机会并把 K 线统一截断到运行 `end`。
- 同一 symbol 的全部买点画在一张 PNG，使用竖向虚线加向上箭头；每两个月显示时间竖线和 `YYYY-MM`。
- 卖出研究用 `opportunity_date` 当天 open 作为 `entry_price`，如果 open 缺失才回退 close，避免未来函数。
- 卖出研究不把缺失机会日 K 线顺延为后续入场，初始止损从入场日开始检查。
- 不可交易的退出行不绘制虚假 marker，也不能中断其他 symbol 出图。
- 涨停基因使用主板 9.8%、创业板/科创板 19.8% 的分板阈值。
- 近两年涨跌停统计不读取回测 `end` 之后的日线。
- 大牛股模式退出不设置固定止盈；趋势模式启动后，以 MA20 连续跌破和 ATR chandelier 作为主要退出，目标是不提前错过主升段。
- 全市场运行前先通过 sample 运行，防止一次性生成大量错误 PNG。

## First Production Run

已完成第一次全量运行：

```bash
python3 -m stock.run.sample_pipeline --universe all --signal-type ma5_ma10_big_bull --start 2024-08-01 --end 2026-08-01 --merge-existing --run-id 20260801_ma5_ma10_big_bull
```

卖出研究：

```bash
python3 -m stock.backtest.big_bull_exit_research --opportunity-csv stock/report/opportunities/20260801_ma5_ma10_big_bull_stock_signal_opportunities.csv --data-root stock/data/origin --run-id 20260801_ma5_ma10_big_bull_exit
```

核心交付检查：

- `stock/report/opportunities/20260801_ma5_ma10_big_bull_stock_signal_opportunities.csv`
- `stock/report/opportunities_date_ma5_ma10_big/20260801_ma5_ma10_big_bull/`
- `stock/analysis/20260801_ma5_ma10_big_bull/charts/`
- `stock/report/exit_research/20260801_ma5_ma10_big_bull_exit_big_bull_exit_trades.csv`
- `stock/report/exit_research/20260801_ma5_ma10_big_bull_exit_big_bull_exit_summary.json`
- `stock/analysis/20260801_ma5_ma10_big_bull_exit/charts/`

实际结果：

- 股票数 `4998`，机会数 `4917`，有机会股票 `2863`
- 机会日期 `464` 天，每日最多 `30` 只，symbol 最短机会日冷却 `20` 个自然日
- `big_bull_score` 范围 `70.02` 至 `94.48`，`rs_60d_pct` 范围 `0.7002` 至 `1.0000`
- 逐日机会图 `4917` 张，symbol analysis 图 `2863` 张，缺失 K 线 `0`
- 退出研究 `4917` 笔，买卖点图 `2863` 张，缺失 K 线 `0`
- 平均收益 `0.638%`，未计手续费、滑点、涨跌停成交约束和仓位管理
- 聚焦测试 `43 passed`，完整 `stock` 测试 `66 passed`
- Ruff、`python3 -m compileall -q stock` 与 20 项产物一致性审计全部通过
- 独立代码复审结论：Ready，无 Critical/Important findings
