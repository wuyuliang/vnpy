# 创业板 ETF 开盘价与 EMA5 满仓策略实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用本地 `159915.SZ` 前复权日线完成上一交易日收盘 EMA5 与当日开盘价比较的满仓/空仓回测，并生成与 `20260718_winner_holding_final` 相同样式的交易图。

**Architecture:** 将数据验证和 EMA5 信号、交易记账和绩效汇总放在独立的 `ema5_open_strategy.py`，避免把单资产特殊规则耦合进轮动引擎。CLI 只负责加载本地 CSV、原子写出审计文件并调用现有 `render_trade_charts.py`，不联网、不复制绘图代码。

**Tech Stack:** Python 3.13、pandas、NumPy、Pillow、unittest/pytest、Ruff。

**Design:** `stock/etf/20260719_chuangyeban_ema5_open_design.md`

---

### Task 1: 数据验证和无未来 EMA5 信号

**Files:**
- Create: `stock/etf/ema5_open_strategy.py`
- Create: `stock/etf/tests/test_ema5_open_strategy.py`

- [ ] **Step 1: 写 EMA5 时序和相等边界失败测试**

创建测试夹具和两个测试：

```python
import unittest

import numpy as np
import pandas as pd

from stock.etf.ema5_open_strategy import build_ema5_open_signals, prepare_symbol_bars


class Ema5OpenStrategyTests(unittest.TestCase):
    @staticmethod
    def _bars() -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-02", periods=7)
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * 7,
                "datetime": dates,
                "open": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 14.0],
                "high": [10.2, 10.2, 10.2, 10.2, 10.2, 20.2, 20.2],
                "low": [9.8, 9.8, 9.8, 9.8, 9.8, 9.8, 13.8],
                "close": [10.0, 10.0, 10.0, 10.0, 10.0, 20.0, 20.0],
                "volume": [1_000.0] * 7,
            }
        )

    def test_signal_uses_previous_close_ema5_without_lookahead(self) -> None:
        result = build_ema5_open_signals(
            prepare_symbol_bars(self._bars(), "159915.SZ")
        )

        self.assertTrue(result.loc[:4, "previous_ema5"].isna().all())
        self.assertAlmostEqual(result.loc[5, "previous_ema5"], 10.0)
        self.assertFalse(result.loc[5, "target_invested"])
        self.assertAlmostEqual(result.loc[6, "previous_ema5"], 40.0 / 3.0)
        self.assertTrue(result.loc[6, "target_invested"])

    def test_open_equal_to_previous_ema5_stays_empty(self) -> None:
        result = build_ema5_open_signals(
            prepare_symbol_bars(self._bars(), "159915.SZ")
        )

        self.assertEqual(result.loc[5, "open"], result.loc[5, "previous_ema5"])
        self.assertFalse(result.loc[5, "target_invested"])
```

- [ ] **Step 2: 运行测试并确认红灯**

Run:

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py -q
```

Expected: collection fails with `ModuleNotFoundError: stock.etf.ema5_open_strategy`。

- [ ] **Step 3: 实现最小数据准备和信号函数**

在 `ema5_open_strategy.py` 定义稳定列和以下函数：

```python
from __future__ import annotations

import numpy as np
import pandas as pd

BAR_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume"]
SIGNAL_COLUMNS = BAR_COLUMNS + [
    "ema5",
    "previous_ema5",
    "target_invested",
    "action",
]


def prepare_symbol_bars(daily: pd.DataFrame, symbol: str) -> pd.DataFrame:
    missing = set(BAR_COLUMNS) - set(daily.columns)
    if missing:
        raise ValueError(f"ETF daily data missing columns: {sorted(missing)}")
    frame = daily.loc[daily["symbol"].astype(str).eq(symbol), BAR_COLUMNS].copy()
    if frame.empty:
        raise ValueError(f"ETF daily data missing symbol: {symbol}")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    for column in BAR_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["datetime"].duplicated().any():
        raise ValueError(f"ETF daily data contains duplicate dates for {symbol}")
    prices = frame[["open", "high", "low", "close"]]
    valid = (
        np.isfinite(prices).all(axis=1)
        & (prices > 0).all(axis=1)
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & np.isfinite(frame["volume"])
        & (frame["volume"] >= 0)
    )
    if not valid.all():
        raise ValueError(f"ETF daily data contains invalid OHLCV rows for {symbol}")
    frame = frame.sort_values("datetime", ignore_index=True)
    if len(frame) < 6:
        raise ValueError("ETF daily data requires at least 6 rows for previous EMA5")
    return frame


def build_ema5_open_signals(bars: pd.DataFrame) -> pd.DataFrame:
    result = bars.copy()
    result["ema5"] = result["close"].ewm(
        span=5,
        adjust=False,
        min_periods=5,
    ).mean()
    result["previous_ema5"] = result["ema5"].shift(1)
    result["target_invested"] = (
        result["previous_ema5"].notna()
        & (result["open"] > result["previous_ema5"])
    )
    result["action"] = "flat"
    return result[SIGNAL_COLUMNS]
```

- [ ] **Step 4: 补充重复日期、非法 OHLC 和数据不足测试**

```python
    def test_prepare_rejects_duplicate_dates(self) -> None:
        bars = pd.concat([self._bars(), self._bars().iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate dates"):
            prepare_symbol_bars(bars, "159915.SZ")

    def test_prepare_rejects_non_finite_or_invalid_ohlc(self) -> None:
        for column, value in (("high", np.inf), ("low", 11.0)):
            with self.subTest(column=column, value=value):
                bars = self._bars()
                bars.loc[2, column] = value
                with self.assertRaisesRegex(ValueError, "invalid OHLCV"):
                    prepare_symbol_bars(bars, "159915.SZ")

    def test_prepare_requires_six_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 6 rows"):
            prepare_symbol_bars(self._bars().iloc[:5], "159915.SZ")
```

- [ ] **Step 5: 运行测试并提交**

Run:

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py -q
python3 -m ruff check stock/etf/ema5_open_strategy.py stock/etf/tests/test_ema5_open_strategy.py
```

Expected: 5 tests pass，Ruff 输出 `All checks passed!`。

Commit:

```bash
git add stock/etf/ema5_open_strategy.py stock/etf/tests/test_ema5_open_strategy.py
git commit -m "feat: add previous-day EMA5 open signals"
```

### Task 2: 满仓成交、每日权益和绩效汇总

**Files:**
- Modify: `stock/etf/ema5_open_strategy.py`
- Modify: `stock/etf/tests/test_ema5_open_strategy.py`

- [ ] **Step 1: 写满仓轮次和末日开放持仓失败测试**

在测试文件导入新类型，并增加一个会产生 `buy/sell/buy` 的夹具：

```python
from stock.etf.ema5_open_strategy import (
    Ema5OpenConfig,
    build_ema5_open_signals,
    prepare_symbol_bars,
    run_ema5_open_backtest,
)

    @staticmethod
    def _round_trip_bars() -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-02", periods=8)
        opens = [10.0, 10.0, 10.0, 10.0, 10.0, 11.0, 9.0, 12.0]
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 13.0]
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * 8,
                "datetime": dates,
                "open": opens,
                "high": [max(open_, close) + 0.2 for open_, close in zip(opens, closes, strict=True)],
                "low": [min(open_, close) - 0.2 for open_, close in zip(opens, closes, strict=True)],
                "close": closes,
                "volume": [1_000.0] * 8,
            }
        )

    def test_backtest_executes_full_position_round_trip_and_keeps_final_position(self) -> None:
        config = Ema5OpenConfig(
            initial_capital=10_000.0,
            lot_size=100,
            commission_rate=0.0,
            min_commission=0.0,
            slippage_rate=0.0,
        )

        result = run_ema5_open_backtest(self._round_trip_bars(), config)

        self.assertEqual(result.trades["side"].tolist(), ["buy", "sell", "buy"])
        self.assertEqual(result.trades["quantity"].tolist(), [900, 900, 600])
        self.assertAlmostEqual(result.trades.loc[1, "realized_pnl"], -1_800.0)
        self.assertAlmostEqual(result.equity_curve.iloc[-1]["equity"], 8_800.0)
        self.assertEqual(result.positions.iloc[-1]["quantity"], 600)
        self.assertTrue(result.summary["is_open"])
```

- [ ] **Step 2: 运行单测并确认红灯**

Run:

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py::Ema5OpenStrategyTests::test_backtest_executes_full_position_round_trip_and_keeps_final_position -q
```

Expected: import fails because `Ema5OpenConfig` and `run_ema5_open_backtest` do not exist。

- [ ] **Step 3: 实现配置、结果类型和最大可买数量**

在策略模块增加：

```python
from dataclasses import dataclass
from math import floor
from typing import Any

from .portfolio import Portfolio
from .config import StrategyConfig

TRADE_COLUMNS = [
    "datetime", "symbol", "side", "quantity", "raw_price", "fill_price",
    "commission", "slippage_cost", "primary_reason", "all_reasons",
    "realized_pnl",
]
POSITION_COLUMNS = [
    "datetime", "symbol", "quantity", "average_price", "market_value",
    "equity", "weight",
]
EQUITY_COLUMNS = [
    "datetime", "cash", "market_value", "equity", "daily_return", "drawdown",
]


@dataclass(frozen=True)
class Ema5OpenConfig:
    symbol: str = "159915.SZ"
    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    slippage_rate: float = 0.0005

    def __post_init__(self) -> None:
        if self.initial_capital <= 0 or self.lot_size <= 0:
            raise ValueError("initial_capital and lot_size must be positive")
        if min(self.commission_rate, self.min_commission, self.slippage_rate) < 0:
            raise ValueError("execution costs must not be negative")


@dataclass
class Ema5OpenResult:
    signals: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: dict[str, Any]


def _portfolio_config(config: Ema5OpenConfig) -> StrategyConfig:
    return StrategyConfig(
        initial_capital=config.initial_capital,
        lot_size=config.lot_size,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        slippage_rate=config.slippage_rate,
        max_position_weight=1.0,
    )


def calculate_full_position_quantity(
    available_cash: float,
    raw_price: float,
    config: Ema5OpenConfig,
) -> int:
    fill_price = raw_price * (1 + config.slippage_rate)
    quantity = floor(available_cash / fill_price / config.lot_size) * config.lot_size
    while quantity > 0:
        notional = quantity * fill_price
        commission = max(notional * config.commission_rate, config.min_commission)
        if notional + commission <= available_cash + 1e-9:
            return quantity
        quantity -= config.lot_size
    return 0
```

- [ ] **Step 4: 实现逐日回测和摘要**

实现 `run_ema5_open_backtest(bars, config)`：

```python
def run_ema5_open_backtest(
    bars: pd.DataFrame,
    config: Ema5OpenConfig | None = None,
) -> Ema5OpenResult:
    cfg = config or Ema5OpenConfig()
    prepared = prepare_symbol_bars(bars, cfg.symbol)
    signals = build_ema5_open_signals(prepared)
    portfolio = Portfolio(cfg.initial_capital, _portfolio_config(cfg))
    position_rows: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []
    previous_equity = cfg.initial_capital
    peak_equity = cfg.initial_capital

    for index, row in signals.iterrows():
        invested = cfg.symbol in portfolio.positions
        target = bool(row["target_invested"])
        action = "hold" if invested else "flat"
        if target and not invested:
            quantity = calculate_full_position_quantity(portfolio.cash, row["open"], cfg)
            if quantity <= 0:
                raise ValueError("available cash cannot buy one whole ETF lot")
            portfolio.buy(
                cfg.symbol, row["datetime"], row["open"], quantity, 0.0,
                "open_above_previous_ema5",
            )
            action = "buy"
        elif not target and invested:
            portfolio.sell(
                cfg.symbol, row["datetime"], row["open"],
                "open_at_or_below_previous_ema5",
            )
            action = "sell"
        signals.loc[index, "action"] = action

        position = portfolio.positions.get(cfg.symbol)
        market_value = position.quantity * row["close"] if position else 0.0
        equity = portfolio.cash + market_value
        daily_return = equity / previous_equity - 1 if previous_equity else 0.0
        peak_equity = max(peak_equity, equity)
        equity_rows.append(
            {
                "datetime": row["datetime"],
                "cash": portfolio.cash,
                "market_value": market_value,
                "equity": equity,
                "daily_return": daily_return,
                "drawdown": equity / peak_equity - 1,
            }
        )
        if position:
            position_rows.append(
                {
                    "datetime": row["datetime"],
                    "symbol": cfg.symbol,
                    "quantity": position.quantity,
                    "average_price": position.average_price,
                    "market_value": market_value,
                    "equity": equity,
                    "weight": market_value / equity,
                }
            )
        previous_equity = equity

    trades = pd.DataFrame(
        [trade.to_dict() for trade in portfolio.trades],
        columns=TRADE_COLUMNS,
    )
    positions = pd.DataFrame(position_rows, columns=POSITION_COLUMNS)
    equity_curve = pd.DataFrame(equity_rows, columns=EQUITY_COLUMNS)
    summary = _build_summary(equity_curve, trades, cfg, cfg.symbol in portfolio.positions)
    return Ema5OpenResult(signals, trades, positions, equity_curve, summary)
```

实现 `_build_summary`，公式与现有 `backtest._build_summary` 一致：252 日年化、总体标准差、零无风险利率 Sharpe、卖出交易胜率：

```python
def _build_summary(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    config: Ema5OpenConfig,
    is_open: bool,
) -> dict[str, Any]:
    final_equity = float(equity_curve.iloc[-1]["equity"])
    total_return = final_equity / config.initial_capital - 1
    years = max(len(equity_curve) / 252, 1 / 252)
    annual_return = (
        (1 + total_return) ** (1 / years) - 1
        if total_return > -1
        else -1.0
    )
    returns = equity_curve["daily_return"]
    daily_std = float(returns.std(ddof=0))
    annual_volatility = daily_std * np.sqrt(252)
    sharpe = (
        float(returns.mean() / daily_std * np.sqrt(252))
        if daily_std > 0
        else 0.0
    )
    sells = trades.loc[trades["side"].eq("sell")]
    return {
        "symbol": config.symbol,
        "requested_period": "recent_10_years",
        "actual_start_date": str(
            pd.Timestamp(equity_curve.iloc[0]["datetime"]).date()
        ),
        "actual_end_date": str(
            pd.Timestamp(equity_curve.iloc[-1]["datetime"]).date()
        ),
        "initial_capital": config.initial_capital,
        "final_equity": final_equity,
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": float(equity_curve["drawdown"].min()),
        "trade_count": int(len(trades)),
        "completed_round_trips": int(len(sells)),
        "win_rate": (
            float(sells["realized_pnl"].gt(0).mean())
            if not sells.empty
            else 0.0
        ),
        "total_commission": float(trades["commission"].sum()),
        "total_slippage_cost": float(trades["slippage_cost"].sum()),
        "is_open": is_open,
    }
```

- [ ] **Step 5: 补充成本、整数手和不重复交易测试**

```python
    def test_full_position_quantity_includes_slippage_and_commission(self) -> None:
        config = Ema5OpenConfig(initial_capital=100_000.0)
        quantity = calculate_full_position_quantity(100_000.0, 100.0, config)
        fill = 100.0 * (1 + config.slippage_rate)
        cost = quantity * fill + max(
            quantity * fill * config.commission_rate,
            config.min_commission,
        )
        next_quantity = quantity + config.lot_size
        next_cost = next_quantity * fill + max(
            next_quantity * fill * config.commission_rate,
            config.min_commission,
        )

        self.assertEqual(quantity % 100, 0)
        self.assertLessEqual(cost, 100_000.0)
        self.assertGreater(next_cost, 100_000.0)

    def test_unchanged_long_signal_does_not_repeat_buy(self) -> None:
        bars = self._round_trip_bars()
        bars.loc[6:, "open"] = 12.0
        bars.loc[6:, "high"] = 13.2
        result = run_ema5_open_backtest(
            bars,
            Ema5OpenConfig(
                initial_capital=10_000.0,
                commission_rate=0.0,
                min_commission=0.0,
                slippage_rate=0.0,
            ),
        )
        self.assertEqual(result.trades["side"].tolist(), ["buy"])
```

- [ ] **Step 6: 运行策略测试和提交**

Run:

```bash
python3 -m pytest stock/etf/tests/test_ema5_open_strategy.py -q
python3 -m ruff check stock/etf/ema5_open_strategy.py stock/etf/tests/test_ema5_open_strategy.py
```

Expected: all tests pass，Ruff 无错误。

Commit:

```bash
git add stock/etf/ema5_open_strategy.py stock/etf/tests/test_ema5_open_strategy.py
git commit -m "feat: backtest full-position EMA5 open strategy"
```

### Task 3: 本地 CLI、审计输出和交易图

**Files:**
- Create: `stock/etf/run_ema5_open_strategy.py`
- Create: `stock/etf/tests/test_run_ema5_open_strategy.py`
- Reuse without modification: `stock/etf/render_trade_charts.py`

- [ ] **Step 1: 写端到端输出失败测试**

测试在临时目录创建日线和元数据，直接运行公开入口：

```python
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from PIL import Image

from stock.etf.ema5_open_strategy import Ema5OpenConfig
from stock.etf.run_ema5_open_strategy import run_and_write


class RunEma5OpenStrategyTests(unittest.TestCase):
    def test_run_writes_audits_and_winner_holding_style_chart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            daily_path = root / "daily.csv"
            metadata_path = root / "metadata.csv"
            output_dir = root / "output"
            dates = pd.bdate_range("2026-01-02", periods=8)
            opens = [10.0, 10.0, 10.0, 10.0, 10.0, 11.0, 9.0, 12.0]
            closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 13.0]
            pd.DataFrame(
                {
                    "symbol": ["159915.SZ"] * 8,
                    "datetime": dates,
                    "open": opens,
                    "high": [max(a, b) + 0.2 for a, b in zip(opens, closes, strict=True)],
                    "low": [min(a, b) - 0.2 for a, b in zip(opens, closes, strict=True)],
                    "close": closes,
                    "volume": [1_000.0] * 8,
                }
            ).to_csv(daily_path, index=False)
            pd.DataFrame(
                {
                    "symbol": ["159915.SZ"],
                    "name": ["易方达创业板ETF"],
                    "fund_type": ["股票型ETF"],
                }
            ).to_csv(metadata_path, index=False)

            summary = run_and_write(
                daily_csv=daily_path,
                metadata_csv=metadata_path,
                output_dir=output_dir,
                config=Ema5OpenConfig(
                    initial_capital=10_000.0,
                    commission_rate=0.0,
                    min_commission=0.0,
                    slippage_rate=0.0,
                ),
                overwrite=True,
            )

            for filename in (
                "summary.json", "equity_curve.csv", "signals.csv",
                "trades.csv", "positions.csv",
            ):
                self.assertTrue((output_dir / filename).is_file(), filename)
            image_path = output_dir / "charts/0001_159915_SZ_易方达创业板ETF.png"
            self.assertTrue(image_path.is_file())
            self.assertEqual(Image.open(image_path).size, (1680, 1000))
            self.assertEqual(summary["actual_start_date"], "2026-01-02")
            self.assertEqual(summary["actual_end_date"], "2026-01-13")
            self.assertEqual(
                json.loads((output_dir / "summary.json").read_text())["trade_count"],
                3,
            )
```

- [ ] **Step 2: 运行端到端测试并确认红灯**

Run:

```bash
python3 -m pytest stock/etf/tests/test_run_ema5_open_strategy.py -q
```

Expected: collection fails with `ModuleNotFoundError: stock.etf.run_ema5_open_strategy`。

- [ ] **Step 3: 实现原子输出和运行入口**

在 runner 中定义默认路径和原子写函数：

```python
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .ema5_open_strategy import Ema5OpenConfig, run_ema5_open_backtest
from .render_trade_charts import render_all_trade_charts

ETF_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ETF_ROOT / "data/20260717_2018_20260717_point_in_time_live"
DEFAULT_DAILY_CSV = DEFAULT_SOURCE_ROOT / "etfs_lifecycle_clean.csv"
DEFAULT_METADATA_CSV = DEFAULT_SOURCE_ROOT / "metadata.csv"
DEFAULT_OUTPUT_DIR = ETF_ROOT / "output/20260719_chuangyeban"


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
```

实现 `run_and_write`：加载两个 CSV，验证元数据存在且名称非空，运行回测，原子写五个
文件，再调用图形渲染器：

```python
def run_and_write(
    *,
    daily_csv: Path,
    metadata_csv: Path,
    output_dir: Path,
    config: Ema5OpenConfig | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    cfg = config or Ema5OpenConfig()
    daily = pd.read_csv(daily_csv)
    metadata = pd.read_csv(metadata_csv)
    selected = metadata.loc[metadata["symbol"].astype(str).eq(cfg.symbol)]
    if len(selected) != 1 or not str(selected.iloc[0].get("name", "")).strip():
        raise ValueError(f"metadata requires one named row for {cfg.symbol}")
    result = run_ema5_open_backtest(daily, cfg)
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_csv(result.signals, output_dir / "signals.csv")
    _atomic_write_csv(result.trades, output_dir / "trades.csv")
    _atomic_write_csv(result.positions, output_dir / "positions.csv")
    _atomic_write_csv(result.equity_curve, output_dir / "equity_curve.csv")
    summary = {
        **result.summary,
        "daily_csv": str(daily_csv),
        "metadata_csv": str(metadata_csv),
        "output_dir": str(output_dir),
        "parameters": {
            "ema_period": 5,
            "initial_capital": cfg.initial_capital,
            "lot_size": cfg.lot_size,
            "commission_rate": cfg.commission_rate,
            "min_commission": cfg.min_commission,
            "slippage_rate": cfg.slippage_rate,
        },
    }
    _atomic_write_json(summary, output_dir / "summary.json")
    render_all_trade_charts(
        daily_csv=daily_csv,
        metadata_csv=metadata_csv,
        trades_csv=output_dir / "trades.csv",
        positions_csv=output_dir / "positions.csv",
        output_dir=output_dir / "charts",
        report_start=result.equity_curve.iloc[0]["datetime"],
        report_end=result.equity_curve.iloc[-1]["datetime"],
        overwrite=overwrite,
    )
    return summary
```

- [ ] **Step 4: 实现 CLI 参数和默认命令**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-csv", type=Path, default=DEFAULT_DAILY_CSV)
    parser.add_argument("--metadata-csv", type=Path, default=DEFAULT_METADATA_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--symbol", default="159915.SZ")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_and_write(
        daily_csv=args.daily_csv,
        metadata_csv=args.metadata_csv,
        output_dir=args.output_dir,
        config=Ema5OpenConfig(symbol=args.symbol, initial_capital=args.initial_capital),
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 运行端到端和全量测试并提交**

Run:

```bash
python3 -m pytest stock/etf/tests/test_run_ema5_open_strategy.py -q
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
```

Expected: end-to-end and full ETF tests pass，Ruff lint/format 无错误。

Commit:

```bash
git add stock/etf/run_ema5_open_strategy.py stock/etf/tests/test_run_ema5_open_strategy.py
git commit -m "feat: output 创业板 EMA5 strategy review"
```

### Task 4: 真实本地回测、图形审计和最终复核

**Files:**
- Generate: `stock/etf/output/20260719_chuangyeban/**`
- Review: `stock/etf/ema5_open_strategy.py`
- Review: `stock/etf/run_ema5_open_strategy.py`

- [ ] **Step 1: 执行真实回测和覆盖图形**

Run:

```bash
python3 -m stock.etf.run_ema5_open_strategy --overwrite
```

Expected: exit code 0，控制台摘要中的 `symbol` 为 `159915.SZ`，实际日期为
`2017-08-14` 至 `2026-07-17`，并生成一张 PNG。

- [ ] **Step 2: 审计交易、权益和摘要一致性**

运行只读 pandas 审计：

```bash
python3 -c '
import json
from pathlib import Path
import numpy as np
import pandas as pd
root = Path("stock/etf/output/20260719_chuangyeban")
summary = json.loads((root / "summary.json").read_text())
signals = pd.read_csv(root / "signals.csv", parse_dates=["datetime"])
trades = pd.read_csv(root / "trades.csv", parse_dates=["datetime"])
positions = pd.read_csv(root / "positions.csv", parse_dates=["datetime"])
equity = pd.read_csv(root / "equity_curve.csv", parse_dates=["datetime"])
assert signals["datetime"].is_unique
assert equity["datetime"].is_unique
assert np.isfinite(equity[["cash", "market_value", "equity", "daily_return", "drawdown"]]).all().all()
assert (trades["quantity"] % 100 == 0).all()
assert (trades["quantity"] > 0).all()
assert len(trades) == summary["trade_count"]
assert abs(equity.iloc[-1]["equity"] - summary["final_equity"]) < 1e-6
assert abs(equity["drawdown"].min() - summary["max_drawdown"]) < 1e-12
assert (root / "charts/0001_159915_SZ_易方达创业板ETF.png").is_file()
print(json.dumps({
    "rows": len(equity),
    "trades": len(trades),
    "start": str(equity["datetime"].min().date()),
    "end": str(equity["datetime"].max().date()),
    "total_return": summary["total_return"],
    "annual_return": summary["annual_return"],
    "max_drawdown": summary["max_drawdown"],
    "sharpe": summary["sharpe"],
    "is_open": summary["is_open"],
}, ensure_ascii=False, indent=2))
'
```

Expected: assertions all pass，并打印真实收益摘要。

- [ ] **Step 3: 视觉检查图形**

使用 `view_image` 打开：

```text
stock/etf/output/20260719_chuangyeban/charts/0001_159915_SZ_易方达创业板ETF.png
```

确认标题、中文名称、周线、日线、成交量、买卖点和左侧交易统计均完整，买卖标签数量
与 `trades.csv` 一致，无裁切、乱码或空白面板。

- [ ] **Step 4: 最终质量门和代码复核**

Run:

```bash
python3 -m pytest stock/etf/tests -q
python3 -m ruff check stock/etf
python3 -m ruff format --check stock/etf
python3 -m compileall -q stock/etf
git diff --check
```

Expected: all ETF tests pass，Ruff lint/format、compileall 和 whitespace check 均通过。

复核重点：

- `target_invested` 只使用 `previous_ema5`，没有 `EMA5_T` 或下一日价格。
- 买入时滑点和最低佣金均纳入最大整数手现金约束。
- 卖出已实现盈亏不重复扣除滑点。
- 未平仓头寸只按末日收盘估值，不生成强制卖出交易。
- 输出目录只有本策略文件和一张交易图，不覆盖 `winner_holding_final`。

- [ ] **Step 5: 提交最终修复（如复核产生改动）**

仅当 Task 4 发现并修复问题时执行：

```bash
git add stock/etf/ema5_open_strategy.py stock/etf/run_ema5_open_strategy.py \
  stock/etf/tests/test_ema5_open_strategy.py stock/etf/tests/test_run_ema5_open_strategy.py
git commit -m "fix: harden 创业板 EMA5 strategy outputs"
```

不要提交 `stock/etf/output/20260719_chuangyeban/` 中生成的 CSV、JSON 或 PNG。
