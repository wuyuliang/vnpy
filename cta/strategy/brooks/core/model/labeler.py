"""标注:setup 时点向前看 N 根 bar,判断是否达到目标 R:R。

规则:
- 入场价 entry = bar.close(setup 触发 bar 的收盘)
- 初始止损距离 stop_distance = entry - stop_price
- target_price = entry + target_rr * stop_distance
- 向前 N 根 bar 内:
    - 若先触达 target_price → label = 1 (win)
    - 若先触达 stop_price   → label = 0 (loss)
    - 未触达 → 按 N 根后 close 判定:close >= entry + 0.5*stop_distance 记 win 否则 loss
      (避免因窗口不够大而抛弃样本)

输出:dict 含 label / mfe / mae / holding_bars / exit_reason
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

ExitKind = Literal["target", "stop", "timeout_win", "timeout_loss", "nan"]


@dataclass
class Label:
    label: int                   # 0 / 1
    mfe: float                   # 最大有利变动(价格)
    mae: float                   # 最大不利变动(价格)
    holding_bars: int
    exit_price: float
    exit_kind: ExitKind


def label_setup(
    ohlc: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    target_rr: float = 2.0,
    target_bars: int = 20,
    direction: int = 1,
) -> Label:
    """在 ohlc 的 entry_idx 之后 target_bars 根 bar 内判定输赢。

    ohlc 必须含 high/low/close 列,按时间升序。entry_idx 本身不参与判定。
    """
    if direction != 1:
        raise NotImplementedError("v3 仅做多方标注")

    stop_distance = entry_price - stop_price
    if stop_distance <= 0 or math.isnan(stop_distance):
        return Label(0, 0.0, 0.0, 0, entry_price, "nan")

    target_price = entry_price + target_rr * stop_distance

    end = min(entry_idx + 1 + target_bars, len(ohlc))
    mfe = 0.0
    mae = 0.0
    for j in range(entry_idx + 1, end):
        high = float(ohlc.iloc[j]["high"])
        low = float(ohlc.iloc[j]["low"])
        # 更新 MFE / MAE
        mfe = max(mfe, high - entry_price)
        mae = min(mae, low - entry_price)
        # 优先判定:触止损
        if low <= stop_price:
            return Label(0, mfe, mae, j - entry_idx, stop_price, "stop")
        # 再判定:触目标
        if high >= target_price:
            return Label(1, mfe, mae, j - entry_idx, target_price, "target")

    # 未触达:按结束时 close 判定
    if end - 1 <= entry_idx:
        return Label(0, mfe, mae, 0, entry_price, "nan")
    last_close = float(ohlc.iloc[end - 1]["close"])
    won = last_close >= entry_price + 0.5 * stop_distance
    return Label(
        label=1 if won else 0,
        mfe=mfe, mae=mae,
        holding_bars=end - 1 - entry_idx,
        exit_price=last_close,
        exit_kind="timeout_win" if won else "timeout_loss",
    )


__all__ = ["Label", "label_setup"]
