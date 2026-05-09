"""回测↔仿真信号一致性校验。

把两侧的信号序列（``SignalRecord``）按时间近邻匹配，输出失配率与详细差异。
"判断策略是否真的可上实盘" 的核心门槛：在同样数据下，仿真信号应与回测信号
失配率 < 阈值（建议 5%）。

使用流程
--------
1. 从回测的 ``trade_log`` 用 ``trade_log_to_signals`` 抽信号
2. 从仿真侧抓订单/成交流水（``vnpy.trader.event.EVENT_ORDER`` 或日志），同样转 ``SignalRecord``
3. 调 ``compare_signals(a, b, time_tolerance=...)`` 得 ``ParityResult``
4. 若 ``mismatch_rate`` 超阈值，从 ``details`` 定位差异行
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd


@dataclass
class SignalRecord:
    """单条信号的最小描述。"""

    timestamp: pd.Timestamp
    side: str            # long / short / flat
    lots: int
    order_type: str = "market"
    price: float | None = None


@dataclass
class ParityResult:
    matched: int
    mismatched: int      # 时间对得上但内容（side/lots）不一致
    only_in_a: int
    only_in_b: int
    mismatch_rate: float  # (mismatched + only_in_a + only_in_b) / total
    details: pd.DataFrame  # 每行一条差异


def _record_eq(x: SignalRecord, y: SignalRecord) -> bool:
    return (
        str(x.side).lower() == str(y.side).lower()
        and int(x.lots) == int(y.lots)
        and str(x.order_type).lower() == str(y.order_type).lower()
    )


def compare_signals(
    a: Sequence[SignalRecord],
    b: Sequence[SignalRecord],
    *,
    time_tolerance: pd.Timedelta = pd.Timedelta("1min"),
) -> ParityResult:
    """按时间最近邻匹配 a/b 两侧信号，超出 tolerance 视为单边。

    复杂度 O(N log N) by sort + two-pointer。N = max(len(a), len(b))。
    """
    a_sorted = sorted(a, key=lambda r: r.timestamp)
    b_sorted = sorted(b, key=lambda r: r.timestamp)
    used_b: set[int] = set()
    matched = 0
    mismatched = 0
    only_in_a = 0
    detail_rows: list[dict] = []

    j_start = 0
    for x in a_sorted:
        # 在 b 中找最近且在 tolerance 内、尚未占用的
        best = -1
        best_diff: pd.Timedelta | None = None
        for j in range(j_start, len(b_sorted)):
            if j in used_b:
                continue
            diff = abs(x.timestamp - b_sorted[j].timestamp)
            if diff > time_tolerance:
                if b_sorted[j].timestamp < x.timestamp - time_tolerance:
                    j_start = j + 1
                continue
            if best_diff is None or diff < best_diff:
                best = j
                best_diff = diff
        if best < 0:
            only_in_a += 1
            detail_rows.append({
                "type": "only_in_a",
                "timestamp": x.timestamp,
                "side_a": x.side, "lots_a": x.lots, "order_a": x.order_type,
                "side_b": "", "lots_b": 0, "order_b": "",
            })
            continue
        used_b.add(best)
        y = b_sorted[best]
        if _record_eq(x, y):
            matched += 1
        else:
            mismatched += 1
            detail_rows.append({
                "type": "mismatch",
                "timestamp": x.timestamp,
                "side_a": x.side, "lots_a": x.lots, "order_a": x.order_type,
                "side_b": y.side, "lots_b": y.lots, "order_b": y.order_type,
            })

    only_in_b = 0
    for j, y in enumerate(b_sorted):
        if j in used_b:
            continue
        only_in_b += 1
        detail_rows.append({
            "type": "only_in_b",
            "timestamp": y.timestamp,
            "side_a": "", "lots_a": 0, "order_a": "",
            "side_b": y.side, "lots_b": y.lots, "order_b": y.order_type,
        })

    total = matched + mismatched + only_in_a + only_in_b
    rate = (mismatched + only_in_a + only_in_b) / total if total > 0 else 0.0
    return ParityResult(
        matched=matched,
        mismatched=mismatched,
        only_in_a=only_in_a,
        only_in_b=only_in_b,
        mismatch_rate=rate,
        details=pd.DataFrame(detail_rows),
    )


def trade_log_to_signals(
    trade_log: pd.DataFrame,
    dates: Sequence[pd.Timestamp],
) -> list[SignalRecord]:
    """v1 ``trade_log`` 转信号序列。

    对每笔交易输出 2 条信号：
        - entry_i  → side = long/short, lots
        - exit_i   → side = flat, lots
    """
    if trade_log.empty:
        return []
    dt_index = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    out: list[SignalRecord] = []
    for _, row in trade_log.iterrows():
        ei = int(row["entry_i"])
        xi = int(row["exit_i"])
        side = str(row.get("side", "")).lower()
        lots = int(row.get("lots", 0))
        if 0 <= ei < len(dt_index):
            out.append(SignalRecord(
                timestamp=dt_index[ei],
                side=side,
                lots=lots,
                order_type="market",
                price=float(row["entry_price"]) if "entry_price" in row else None,
            ))
        if 0 <= xi < len(dt_index):
            out.append(SignalRecord(
                timestamp=dt_index[xi],
                side="flat",
                lots=lots,
                order_type="market",
                price=float(row["exit_price"]) if "exit_price" in row else None,
            ))
    return out


__all__ = [
    "ParityResult",
    "SignalRecord",
    "compare_signals",
    "trade_log_to_signals",
]
