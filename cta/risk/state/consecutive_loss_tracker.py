"""ConsecutiveLossGuard 状态：按分组键存最近 N 笔 win/loss + cooldown 截止。

设计：
- 分组 key 由 cfg.apply_to_groups 决定（默认 cluster|symbol|signal_type）
- on_trade：append 一笔结果（win=True / loss=False，由 net_pnl > 0 判定）
- is_in_cooldown：查当前 key 是否在 cooldown 期内
- cooldown_until：分组键 → cooldown 截止 timestamp（None 表示不在 cooldown）

持久化：JSON 文件，每日 save() 一次（与 BucketPnlTracker 同款约定）。
fail-open：load 失败 → 空 tracker。
"""
from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.risk.base import normalize_cluster, normalize_symbol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LossKey:
    """分组键。字段顺序固定（cluster / symbol / signal_type / interval）。"""

    cluster: str = ""
    symbol: str = ""
    signal_type: str = ""
    interval: str = ""

    def to_str(self) -> str:
        return "|".join(
            v if v else "_" for v in (self.cluster, self.symbol, self.signal_type, self.interval)
        )

    @classmethod
    def from_str(cls, s: str) -> "LossKey":
        parts = str(s).split("|")
        while len(parts) < 4:
            parts.append("_")
        return cls(
            cluster=parts[0] if parts[0] != "_" else "",
            symbol=parts[1] if parts[1] != "_" else "",
            signal_type=parts[2] if parts[2] != "_" else "",
            interval=parts[3] if parts[3] != "_" else "",
        )


class ConsecutiveLossTracker:
    """连续亏损追踪 + cooldown 状态。"""

    def __init__(
        self,
        *,
        n_consecutive_losses: int = 3,
        cooldown_hours: int = 24,
        lookback_days: int = 7,
        apply_to_groups: tuple[str, ...] = ("cluster", "symbol", "signal_type"),
        state_path: str | Path | None = None,
    ) -> None:
        self.n_consecutive_losses = int(n_consecutive_losses)
        self.cooldown_hours = int(cooldown_hours)
        self.lookback_days = int(lookback_days)
        self.apply_to_groups = tuple(str(g).strip().lower() for g in apply_to_groups)
        self.state_path = Path(state_path) if state_path else None
        # key → deque[(dt, is_win)]
        self._history: dict[LossKey, deque[tuple[pd.Timestamp, bool]]] = {}
        # key → cooldown_until timestamp
        self._cooldown: dict[LossKey, pd.Timestamp] = {}

    # ── classify ────────────────────────────────────────────────────

    def make_key(
        self,
        *,
        cluster: str = "",
        symbol: str = "",
        signal_type: str = "",
        interval: str = "",
    ) -> LossKey:
        fields = {
            "cluster": normalize_cluster(cluster) if "cluster" in self.apply_to_groups else "",
            "symbol": normalize_symbol(symbol) if "symbol" in self.apply_to_groups else "",
            "signal_type": (
                str(signal_type).strip().lower() if "signal_type" in self.apply_to_groups else ""
            ),
            "interval": (
                str(interval).strip().lower() if "interval" in self.apply_to_groups else ""
            ),
        }
        return LossKey(**fields)

    # ── update ──────────────────────────────────────────────────────

    def on_trade(self, trade: dict) -> None:
        try:
            net_pnl = float(trade.get("net_pnl", 0.0))
            dt = pd.Timestamp(trade.get("dt", pd.Timestamp.now()))
        except (TypeError, ValueError) as exc:
            logger.debug("on_trade skip bad trade %r: %s", trade, exc)
            return
        key = self.make_key(
            cluster=str(trade.get("cluster", "")),
            symbol=str(trade.get("symbol", "")),
            signal_type=str(trade.get("signal_type", "")),
            interval=str(trade.get("interval", "")),
        )
        is_win = net_pnl > 0.0
        self._history.setdefault(key, deque()).append((dt, is_win))
        # 触发 cooldown
        self._maybe_trigger_cooldown(key, now=dt)
        self._evict_for(key, now=dt)

    def _maybe_trigger_cooldown(self, key: LossKey, *, now: pd.Timestamp) -> None:
        dq = self._history.get(key)
        if not dq:
            return
        # 截最近 n 笔
        recent = list(dq)[-self.n_consecutive_losses:]
        if len(recent) < self.n_consecutive_losses:
            return
        # 全是 loss 才触发
        if all(not is_win for _, is_win in recent):
            until = now + pd.Timedelta(hours=int(self.cooldown_hours))
            self._cooldown[key] = until
            logger.info(
                "ConsecutiveLossTracker cooldown: key=%s until=%s after %d losses",
                key.to_str(), until, self.n_consecutive_losses,
            )

    # ── query ───────────────────────────────────────────────────────

    def is_in_cooldown(
        self,
        key: LossKey,
        *,
        now: pd.Timestamp | None = None,
    ) -> tuple[bool, pd.Timestamp | None]:
        until = self._cooldown.get(key)
        if until is None:
            return False, None
        ref = pd.Timestamp(now) if now is not None else pd.Timestamp.now()
        if ref >= until:
            # cooldown 已过 → 自动清理
            self._cooldown.pop(key, None)
            return False, None
        return True, until

    def consecutive_loss_count(
        self,
        key: LossKey,
        *,
        now: pd.Timestamp | None = None,
    ) -> int:
        self._evict_for(key, now=now)
        dq = self._history.get(key)
        if not dq:
            return 0
        count = 0
        for _, is_win in reversed(dq):
            if is_win:
                break
            count += 1
        return count

    def known_keys(self) -> list[LossKey]:
        return list(self._history.keys())

    def replay(self, trades: Iterable[dict]) -> int:
        n = 0
        for t in trades:
            self.on_trade(t)
            n += 1
        return n

    # ── persist ─────────────────────────────────────────────────────

    @classmethod
    def from_path(cls, state_path: str | Path, **kwargs) -> "ConsecutiveLossTracker":
        tracker = cls(state_path=state_path, **kwargs)
        tracker.load()
        return tracker

    def load(self) -> None:
        if not self.state_path:
            return
        p = Path(self.state_path)
        if not p.exists():
            return
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("consecutive_loss_state load failed: %s — empty tracker", exc)
            return
        for raw in payload.get("history", []):
            try:
                key = LossKey.from_str(str(raw.get("key", "")))
                events = [
                    (pd.Timestamp(e.get("dt")), bool(e.get("is_win")))
                    for e in raw.get("events", [])
                    if e.get("dt") is not None
                ]
                if events:
                    self._history[key] = deque(events)
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug("skip bad history row %r: %s", raw, exc)
                continue
        for raw in payload.get("cooldown", []):
            try:
                key = LossKey.from_str(str(raw.get("key", "")))
                until = pd.Timestamp(raw.get("until"))
                self._cooldown[key] = until
            except (TypeError, ValueError, KeyError):
                continue

    def save(self) -> Path | None:
        if not self.state_path:
            return None
        p = Path(self.state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "n_consecutive_losses": self.n_consecutive_losses,
            "cooldown_hours": self.cooldown_hours,
            "lookback_days": self.lookback_days,
            "apply_to_groups": list(self.apply_to_groups),
            "history": [
                {
                    "key": key.to_str(),
                    "events": [{"dt": str(dt), "is_win": bool(w)} for dt, w in dq],
                }
                for key, dq in self._history.items()
            ],
            "cooldown": [
                {"key": key.to_str(), "until": str(until)}
                for key, until in self._cooldown.items()
            ],
        }
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p

    # ── internals ───────────────────────────────────────────────────

    def _evict_for(self, key: LossKey, *, now: pd.Timestamp | None = None) -> None:
        dq = self._history.get(key)
        if not dq:
            return
        ref = pd.Timestamp(now) if now is not None else pd.Timestamp(dq[-1][0])
        cutoff = ref - pd.Timedelta(days=int(self.lookback_days))
        while dq and pd.Timestamp(dq[0][0]) < cutoff:
            dq.popleft()


__all__ = ["ConsecutiveLossTracker", "LossKey", "SCHEMA_VERSION"]
