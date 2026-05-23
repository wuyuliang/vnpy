"""Trading calendar helpers for sim/live (P2-18).

提供：
- 中国期货交易时段判定（日盘 / 夜盘）
- 节假日 / 周末判定
- 涨跌停板边界判定（按 cluster 配置 limit_pct）

接口设计原则
------------
- 节假日列表必须**外部注入**（``holidays_csv`` 或 ``holidays_iterable``），不在代码里
  硬编码 — 因为节假日每年公告调整
- 涨跌停板：复用 [config/symbol_cluster_config.py](../config/symbol_cluster_config.py) 的
  ``infer_symbol_limit_pct``，与训练/OOT 同源
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_limit_pct

logger = logging.getLogger(__name__)


# 商品期货标准交易时段（CST/Asia-Shanghai）
DAY_SESSION_START = time(9, 0)
DAY_SESSION_END = time(15, 0)
NIGHT_SESSION_START = time(21, 0)
# 不同品种夜盘结束时间不同（贵金属 02:30，黑色 23:00 等）；保守用 02:30 上限
NIGHT_SESSION_END_MAX = time(2, 30)


@dataclass(frozen=True)
class SessionInfo:
    """单个 timestamp 的交易时段判定结果。"""
    is_trading_time: bool
    session: str   # "day" / "night" / "off"


def is_weekend(ts: pd.Timestamp | datetime | date) -> bool:
    """周末 (Sat/Sun)。注意：周六凌晨可能是周五夜盘的延续，调用方自己判断。"""
    return int(pd.Timestamp(ts).weekday()) >= 5


def is_holiday(ts: pd.Timestamp | datetime | date, holidays: set[date]) -> bool:
    """节假日。``holidays`` 是 date 集合（由调用方注入）。"""
    return pd.Timestamp(ts).date() in holidays


def is_trading_session(
    ts: pd.Timestamp,
    *,
    has_night_session: bool = True,
) -> SessionInfo:
    """判定一个 timestamp 是否在交易时段内（仅看时间，不看节假日 / 周末）。"""
    t = pd.Timestamp(ts).time()
    # 日盘：09:00-15:00（含午休 11:30-13:30，简化版不剔除）
    if DAY_SESSION_START <= t <= DAY_SESSION_END:
        return SessionInfo(True, "day")
    # 夜盘：21:00 - 次日 02:30
    if has_night_session:
        if t >= NIGHT_SESSION_START:
            return SessionInfo(True, "night")
        if t <= NIGHT_SESSION_END_MAX:
            return SessionInfo(True, "night")
    return SessionInfo(False, "off")


def load_holidays_from_csv(path: Path | str) -> set[date]:
    """从 csv 加载节假日；schema = [trade_date]。"""
    p = Path(path)
    if not p.exists():
        logger.warning("holidays csv not found: %s", p)
        return set()
    df = pd.read_csv(p)
    if "trade_date" not in df.columns and "date" not in df.columns:
        raise KeyError("holidays csv must have trade_date or date column")
    col = "trade_date" if "trade_date" in df.columns else "date"
    return {pd.Timestamp(d).date() for d in pd.to_datetime(df[col], errors="coerce").dropna().tolist()}


def is_trading_day(
    ts: pd.Timestamp | datetime | date,
    *,
    holidays: set[date] | None = None,
) -> bool:
    """是否为交易日（非周末 + 非节假日）。"""
    if is_weekend(ts):
        return False
    if holidays and is_holiday(ts, holidays):
        return False
    return True


def is_at_price_limit(
    *,
    symbol: str,
    current_price: float,
    prev_close: float,
    tolerance_pct: float = 1e-5,
) -> tuple[bool, str]:
    """判定 current_price 是否触及涨跌停板。

    Returns:
        (is_at_limit, direction)
        direction: "up" / "down" / ""
    """
    limit_pct = float(infer_symbol_limit_pct(symbol))
    upper = float(prev_close) * (1.0 + limit_pct - tolerance_pct)
    lower = float(prev_close) * (1.0 - limit_pct + tolerance_pct)
    if float(current_price) >= upper:
        return True, "up"
    if float(current_price) <= lower:
        return True, "down"
    return False, ""


__all__ = [
    "SessionInfo",
    "DAY_SESSION_START",
    "DAY_SESSION_END",
    "NIGHT_SESSION_START",
    "NIGHT_SESSION_END_MAX",
    "is_at_price_limit",
    "is_holiday",
    "is_trading_day",
    "is_trading_session",
    "is_weekend",
    "load_holidays_from_csv",
]
