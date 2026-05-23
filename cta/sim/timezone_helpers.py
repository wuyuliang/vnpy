"""Timezone helpers for sim/live (P2-20).

商品期货时区一致性：

- **vnpy bar.datetime**：naive `datetime`，但实际语义是 ``Asia/Shanghai``（UTC+8）
- **磁盘 parquet "datetime" 列**：通常也是 naive Shanghai 时间
- **OOT trade_log**：同上
- **外部数据源 / 外盘**：可能是 UTC 或本地时区

规则
----
- 所有 sim/live 内部用 **naive Asia/Shanghai**（与 vnpy / OOT 同源）
- 与外部交互时显式注明：``to_utc(ts)`` / ``from_utc(ts)``
- 不允许 mix naive + aware：``ensure_naive_shanghai`` 强制统一
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

SHANGHAI_TZ = timezone(timedelta(hours=8))


def ensure_naive_shanghai(ts: Any) -> pd.Timestamp:
    """把任意输入转成 naive Asia/Shanghai pd.Timestamp（项目内标准）。

    支持：
    - naive datetime / pd.Timestamp（假定已是 Shanghai 时间）→ 原样
    - aware（任何时区）→ 转 Shanghai 后去 tz
    - str → pd.Timestamp 解析
    """
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t
    # aware → 转 Asia/Shanghai → 去 tz
    return t.tz_convert(SHANGHAI_TZ).tz_localize(None)


def to_utc(ts: Any, *, assume_shanghai: bool = True) -> pd.Timestamp:
    """转换到 UTC（aware）；naive 输入默认按 Shanghai 解释。"""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        if assume_shanghai:
            t = t.tz_localize(SHANGHAI_TZ)
        else:
            t = t.tz_localize("UTC")
    return t.tz_convert("UTC")


def from_utc(ts: Any) -> pd.Timestamp:
    """从 UTC aware 转成 naive Shanghai。"""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(SHANGHAI_TZ).tz_localize(None)


def is_timezone_consistent(series: pd.Series) -> tuple[bool, str]:
    """检查 datetime Series 是否全部 naive（或全部 aware 同一 tz）。

    返回 (consistent, message)。sim/live 应该全 naive；OOT 也是 naive。
    支持 object dtype（混合 naive+aware 时 pandas 会 cast 成 object）。
    """
    if len(series) == 0:
        return True, "empty"
    tz_types: set[str] = set()
    # 对 object dtype，逐元素检测（不能一次 to_datetime 否则会强制统一）
    for v in series.head(100):
        try:
            t = pd.Timestamp(v)
        except Exception:  # noqa: BLE001
            continue
        if pd.isna(t):
            continue
        tz_types.add("aware:" + str(t.tzinfo) if t.tzinfo else "naive")
    if len(tz_types) <= 1:
        return True, f"all={tz_types}"
    return False, f"mixed_tz={tz_types}"


__all__ = [
    "SHANGHAI_TZ",
    "ensure_naive_shanghai",
    "from_utc",
    "is_timezone_consistent",
    "to_utc",
]
