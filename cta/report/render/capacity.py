"""资金容量分析（基于 trade_log，不重跑回测）。

思路：每笔交易在 entry_i 处对应的 K 线成交量作为流动性约束。
对每个目标资金规模 capital，按 commission_rate / multiplier / entry_price 估算
"理想手数"，再用 ``volume * liquidity_ratio`` 截断成可执行手数，
按截断比例缩放该笔 net_pnl，得到不同资金规模下的近似 PnL。

输入要求 trade_log 含: entry_i, exit_i, entry_price, lots, net_pnl
bars 含: volume

输出：
    DataFrame[capital, total_pnl, fill_ratio, n_capped]
    - total_pnl: 该资金规模下累计 net_pnl
    - fill_ratio: 平均成交满足比例（0~1，1 表示 ideal_lots ≤ cap）
    - n_capped: 被截断的交易笔数
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def capacity_curve(
    trade_log: pd.DataFrame,
    bars: pd.DataFrame,
    *,
    capital_levels: Iterable[float],
    multiplier: float,
    risk_per_trade: float = 0.01,
    liquidity_ratio: float = 0.1,
) -> pd.DataFrame:
    """Compute capacity curve.

    每笔交易的理想手数 = capital * risk_per_trade / (entry_price * multiplier)，
    再向下取整后与 ``floor(volume * liquidity_ratio)`` 取较小值。
    返回不同 capital 下的总 PnL。
    """
    if trade_log.empty:
        return pd.DataFrame(columns=["capital", "total_pnl", "fill_ratio", "n_capped"])

    need = {"entry_i", "lots", "entry_price", "net_pnl"}
    miss = need - set(trade_log.columns)
    if miss:
        raise KeyError(f"capacity_curve trade_log 缺少列: {miss}")
    if "volume" not in bars.columns:
        raise KeyError("capacity_curve bars 缺少 volume 列")

    bars_vol = bars["volume"].astype(float).reset_index(drop=True)
    n_bars = len(bars_vol)

    rows: list[dict] = []
    for cap in capital_levels:
        cap_v = float(cap)
        if cap_v <= 0:
            continue
        ideal_lots = []
        capped_lots = []
        scaled_pnl = []
        n_capped = 0
        for _, row in trade_log.iterrows():
            entry_i = int(row["entry_i"])
            entry_px = float(row["entry_price"])
            net_pnl_per_lot = float(row["net_pnl"]) / max(int(row["lots"]), 1)
            risk_budget = cap_v * float(risk_per_trade)
            denom = entry_px * float(multiplier)
            ideal = int(risk_budget / denom) if denom > 0 else 0
            if ideal <= 0:
                ideal = 1
            cap_lots = ideal
            if 0 <= entry_i < n_bars:
                vol = float(bars_vol.iloc[entry_i])
                if vol > 0:
                    cap_lots = min(ideal, int(vol * float(liquidity_ratio)))
            cap_lots = max(cap_lots, 0)
            if cap_lots < ideal:
                n_capped += 1
            ideal_lots.append(ideal)
            capped_lots.append(cap_lots)
            scaled_pnl.append(net_pnl_per_lot * cap_lots)

        ideal_arr = np.asarray(ideal_lots, dtype=float)
        capped_arr = np.asarray(capped_lots, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            fill = np.where(ideal_arr > 0, capped_arr / ideal_arr, 1.0)
        rows.append(
            {
                "capital": cap_v,
                "total_pnl": float(np.sum(scaled_pnl)),
                "fill_ratio": float(np.mean(fill)) if fill.size else 1.0,
                "n_capped": int(n_capped),
            }
        )
    return pd.DataFrame(rows)


__all__ = ["capacity_curve"]
