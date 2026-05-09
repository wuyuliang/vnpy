"""桥接 ``TradeRecorder`` ↔ ``cta.sim.parity_check`` / ``cta.live.daily_report``。

设计动机
--------
``parity_check`` 与 ``daily_report`` 都期待 ``trade_log`` (event_driven 格式) +
``equity`` (pd.Series)，但实盘只有 ``TradeRecorder`` 输出的成交流水；本模块负责：

1. ``recorder_to_parity_inputs(recorder, bar_dates, ...)`` —— 把流水按 open→close
   配对成 ``trade_log``，再累计到 ``equity``，并返回与 ``bar_dates`` 对齐的 dates。
2. ``backtest_on_same_bars(strategy_class, vt_symbol, setting, bars)`` —— 同一份
   行情用同一份策略类**重跑回测**，输出回测侧 ``trade_log``；与实盘记录一起喂给
   ``compare_signals`` / ``write_daily_report``，即可量化失配率。

典型用法
--------
    bt_tl = backtest_on_same_bars(SkillTightRangeBreakoutCta, "rb888.SHFE", setting, bars)
    live_tl, live_eq, dates = recorder_to_parity_inputs(adapter.trade_recorder, bars["datetime"])
    res = write_daily_report(
        live_trade_log=live_tl,
        live_equity=live_eq,
        backtest_trade_log=bt_tl,
        dates=dates,
        out_dir="cta/report/live/daily",
        title=f"{vt_symbol} {date.today()}",
    )
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

from cta.run.cta_backtester import _instantiate
from cta.skills.data_backtest.event_driven_backtest import EngineConfig, run_backtest


def trade_log_to_equity(trade_log: pd.DataFrame, *, n_bars: int) -> pd.Series:
    """trade_log 在 ``exit_i`` 累加 net_pnl 形成 equity 序列（长度 n_bars）。"""
    eq = np.zeros(int(n_bars), dtype=float)
    if not trade_log.empty:
        for _, row in trade_log.iterrows():
            xi = int(row.get("exit_i", -1))
            pnl = float(row.get("net_pnl", row.get("gross_pnl", 0.0)) or 0.0)
            if 0 <= xi < n_bars:
                eq[xi] += pnl
    return pd.Series(np.cumsum(eq), name="equity")


def recorder_to_parity_inputs(
    recorder: Any,
    bar_dates: Sequence[pd.Timestamp],
    *,
    multiplier: float = 1.0,
    commission: float = 0.0,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """``TradeRecorder`` → ``(trade_log, equity, dates)``。

    把成交流水按 open→close 配对成 trade_log，对齐到 ``bar_dates`` 的 RangeIndex
    后累加 net_pnl 形成 equity。``dates`` 与 ``equity`` 长度相同。
    """
    raw = recorder.to_dataframe()
    bar_dates = pd.to_datetime(pd.Series(list(bar_dates)).reset_index(drop=True))
    trade_log = recorder.to_trade_log(multiplier=multiplier, commission=commission)
    if trade_log.empty:
        return trade_log, pd.Series(np.zeros(len(bar_dates), dtype=float), name="equity"), bar_dates

    # 把成交 datetime 映射到 bar_dates 上的 index
    if "datetime" not in raw.columns:
        # 退化：用 entry_i / exit_i 直接当 index（适用于已经数值化的输入）
        eq = trade_log_to_equity(trade_log, n_bars=len(bar_dates))
        return trade_log, eq, bar_dates

    dt_index = pd.DatetimeIndex(bar_dates)
    raw_dt = pd.to_datetime(raw["datetime"]).tolist()
    # 把每条 trade row 的 datetime 通过 searchsorted 映射到 bar 索引
    bar_idx_for_raw = dt_index.searchsorted(raw_dt, side="right") - 1
    bar_idx_for_raw = np.clip(bar_idx_for_raw, 0, len(dt_index) - 1)

    # trade_log.entry_i / exit_i 当前是 raw 的行号（来自 to_trade_log 内的 i）；
    # 重新映射为 bar index
    if {"entry_i", "exit_i"}.issubset(trade_log.columns):
        new_log = trade_log.copy()
        new_log["entry_i"] = new_log["entry_i"].map(
            lambda i: int(bar_idx_for_raw[i]) if 0 <= i < len(bar_idx_for_raw) else 0
        )
        new_log["exit_i"] = new_log["exit_i"].map(
            lambda i: int(bar_idx_for_raw[i]) if 0 <= i < len(bar_idx_for_raw) else 0
        )
    else:
        new_log = trade_log

    eq = trade_log_to_equity(new_log, n_bars=len(dt_index))
    return new_log, eq, bar_dates


def backtest_on_same_bars(
    *,
    strategy_class: type,
    vt_symbol: str,
    setting: dict | None,
    bars: pd.DataFrame,
    engine_cfg: EngineConfig | None = None,
) -> pd.DataFrame:
    """用相同的 bars 在 event_driven 引擎上重跑策略，返回 ``trade_log``。

    供 parity_check / daily_report 当作 ``backtest_trade_log`` 使用，
    与实盘 ``TradeRecorder`` 输出对齐。

    实现上只跑回测引擎，不生成 HTML / 蒙特卡洛 / 容量分析（parity 比较用不到，
    跳过这些以提升速度）。
    """
    instance = _instantiate(strategy_class, vt_symbol, dict(setting or {}))
    instance.on_init()
    frame = instance.prepare_frame(bars.copy())
    inner = instance.make_inner(frame)
    cfg = engine_cfg if engine_cfg is not None else EngineConfig()
    out = run_backtest(frame, inner, cfg)
    tl = out["trade_log"]
    expected = ["entry_i", "exit_i", "side", "lots", "entry_price", "exit_price",
                "gross_pnl", "cost", "net_pnl"]
    if tl.empty:
        return pd.DataFrame(columns=expected)
    return tl


__all__ = [
    "backtest_on_same_bars",
    "recorder_to_parity_inputs",
    "trade_log_to_equity",
]
