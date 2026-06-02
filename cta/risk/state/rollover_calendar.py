"""合约换月日历：为 §16.3 RolloverFreezeGuard 提供 (symbol, now) → days_to_expiry /
days_since_main_switch 查询。

文件格式（CSV，路径默认 cta/config/contract_rollover_calendar.csv）：
    symbol,expiry_date,main_switch_date
    RB0,2026-02-15,2026-01-15
    RB2401,2024-01-15,2023-12-01
    ...

约定：
- 字段名固定（symbol/expiry_date/main_switch_date）；其它列被忽略
- expiry_date 必填；main_switch_date 可空（不空时表示该合约由 main_switch_date 起成为主力）
- symbol 大小写不敏感（内部 upper）；未知 symbol 返回 (None, None)
- fail-open：CSV 文件缺失 / 列损坏 → 空日历，guard 自动 fail-open
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from cta.risk.base import normalize_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RolloverEntry:
    symbol: str
    expiry_date: pd.Timestamp
    main_switch_date: pd.Timestamp | None


class RolloverCalendar:
    """轻量 CSV 加载器 + lookup。"""

    def __init__(self, entries: list[RolloverEntry] | None = None) -> None:
        self._by_symbol: dict[str, RolloverEntry] = {}
        for e in entries or []:
            self._by_symbol[normalize_symbol(e.symbol)] = e

    # ── load ────────────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, path: str | Path) -> "RolloverCalendar":
        p = Path(path)
        if not p.exists():
            logger.info("rollover_calendar not found: %s — empty calendar", p)
            return cls()
        try:
            df = pd.read_csv(p, encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            logger.warning("rollover_calendar load failed at %s: %s — empty calendar", p, exc)
            return cls()
        required = {"symbol", "expiry_date"}
        if not required.issubset(df.columns):
            logger.warning(
                "rollover_calendar missing required cols %s — empty calendar",
                required - set(df.columns),
            )
            return cls()
        entries: list[RolloverEntry] = []
        for _, row in df.iterrows():
            sym = normalize_symbol(row.get("symbol"))
            if not sym:
                continue
            exp = pd.to_datetime(row.get("expiry_date"), errors="coerce")
            if pd.isna(exp):
                continue
            ms_raw = row.get("main_switch_date", None)
            ms = pd.to_datetime(ms_raw, errors="coerce") if ms_raw is not None else None
            entries.append(RolloverEntry(
                symbol=sym,
                expiry_date=pd.Timestamp(exp).normalize(),
                main_switch_date=pd.Timestamp(ms).normalize() if ms is not None and not pd.isna(ms) else None,
            ))
        return cls(entries)

    # ── query ───────────────────────────────────────────────────────

    def lookup(self, symbol: str) -> RolloverEntry | None:
        return self._by_symbol.get(normalize_symbol(symbol))

    def days_to_expiry(self, symbol: str, now: pd.Timestamp) -> int | None:
        e = self.lookup(symbol)
        if e is None:
            return None
        delta = (pd.Timestamp(e.expiry_date) - pd.Timestamp(now).normalize()).days
        return int(delta)

    def days_since_main_switch(self, symbol: str, now: pd.Timestamp) -> int | None:
        e = self.lookup(symbol)
        if e is None or e.main_switch_date is None:
            return None
        delta = (pd.Timestamp(now).normalize() - pd.Timestamp(e.main_switch_date)).days
        return int(delta)

    def __len__(self) -> int:
        return len(self._by_symbol)


__all__ = ["RolloverCalendar", "RolloverEntry"]
