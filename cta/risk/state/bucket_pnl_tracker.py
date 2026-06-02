"""(cluster, interval, score_bucket) 滚动 PnL tracker（子系统②）。

设计要点
--------
- **粒度**：(cluster, interval, score_bucket)，桶宽 10pp（用户选项 1）。192 桶（8×6×4），
  实际很多桶长期 trade_count<min 是预期。
- **window**：默认 30 天滚动；超窗的 trade 自动 pop。
- **bp 单位**：滚动 PnL 以"相对账户净值的 bp"为单位 → 跨账户大小通用。caller 在 on_trade
  时提供 ``portfolio_equity`` 用作分母。
- **持久化**：JSON 文件，schema_version + 每桶 trades deque。on_trade 立即 in-mem，
  save() 由 caller 在合适时机（一天一次）调用，避免高频 IO。
- **fail-open**：load 失败 / 文件损坏 → 空 tracker，不影响业务。
"""
from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.risk.base import normalize_cluster, normalize_interval

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BucketKey:
    cluster: str
    interval: str
    score_bucket: str   # "p60-70" | "p70-80" | "p80-90" | "p90+"

    def to_str(self) -> str:
        return f"{self.cluster}|{self.interval}|{self.score_bucket}"

    @classmethod
    def from_str(cls, s: str) -> "BucketKey":
        parts = str(s).split("|")
        if len(parts) != 3:
            raise ValueError(f"BucketKey.from_str expects 'cluster|interval|bucket', got {s!r}")
        return cls(
            cluster=normalize_cluster(parts[0]),
            interval=normalize_interval(parts[1]),
            score_bucket=str(parts[2]).strip().lower(),
        )


def classify_score_bucket(
    score_pctl: float | None,
    edges: tuple[float, ...] = (60.0, 70.0, 80.0, 90.0),
    names: tuple[str, ...] = ("p60-70", "p70-80", "p80-90", "p90+"),
) -> str | None:
    """把 score percentile（0-100）落入桶名。

    < edges[0] → None（不进桶 → caller 不缩仓）。
    > edges[-1] → 最后一个桶。
    长度约定：len(names) == len(edges)。
    """
    if score_pctl is None:
        return None
    try:
        v = float(score_pctl)
    except (TypeError, ValueError):
        return None
    if v < float(edges[0]):
        return None
    last = names[-1]
    for i in range(len(edges) - 1):
        if float(edges[i]) <= v < float(edges[i + 1]):
            return names[i]
    return last


@dataclass
class _BucketRecord:
    dt: pd.Timestamp
    pnl_bp: float       # 已折算到 bp 相对账户净值
    cluster: str
    interval: str
    score_bucket: str
    symbol: str = ""
    raw_pnl: float = 0.0
    raw_equity: float = 0.0

    def to_dict(self) -> dict:
        return {
            "dt": self.dt.isoformat(),
            "pnl_bp": float(self.pnl_bp),
            "cluster": self.cluster,
            "interval": self.interval,
            "score_bucket": self.score_bucket,
            "symbol": self.symbol,
            "raw_pnl": float(self.raw_pnl),
            "raw_equity": float(self.raw_equity),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "_BucketRecord":
        return cls(
            dt=pd.Timestamp(d.get("dt")),
            pnl_bp=float(d.get("pnl_bp", 0.0)),
            cluster=str(d.get("cluster", "")),
            interval=str(d.get("interval", "")),
            score_bucket=str(d.get("score_bucket", "")),
            symbol=str(d.get("symbol", "")),
            raw_pnl=float(d.get("raw_pnl", 0.0)),
            raw_equity=float(d.get("raw_equity", 0.0)),
        )


class BucketPnlTracker:
    """滚动 PnL tracker，按 BucketKey 存 30 天 trade 历史。

    使用：
        tracker = BucketPnlTracker.from_path("cta/run/state/bucket_pnl_state.json")
        tracker.on_trade(trade_dict)   # 收每笔 close trade
        pnl_bp = tracker.rolling_pnl_bp(BucketKey("metal", "day", "p70-80"))
        count = tracker.trade_count(BucketKey(...))
        tracker.save()                   # 每日 flush

    on_trade 接收的 trade dict 必含：
        cluster, interval, score_bucket, dt, net_pnl, portfolio_equity
        （symbol 可选，但便于复盘）
    """

    def __init__(
        self,
        *,
        window_days: int = 30,
        state_path: str | Path | None = None,
    ) -> None:
        self.window_days = int(window_days)
        self.state_path = Path(state_path) if state_path else None
        self._buckets: dict[BucketKey, deque[_BucketRecord]] = {}
        self._last_loaded_at: datetime | None = None

    # ── load / save ─────────────────────────────────────────────────

    @classmethod
    def from_path(
        cls,
        state_path: str | Path,
        *,
        window_days: int = 30,
    ) -> "BucketPnlTracker":
        tracker = cls(window_days=window_days, state_path=state_path)
        tracker.load()
        return tracker

    def load(self) -> None:
        if not self.state_path:
            return
        p = Path(self.state_path)
        if not p.exists():
            logger.info("bucket_pnl_state not found: %s — empty tracker", p)
            return
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bucket_pnl_state load failed: %s — empty tracker", exc)
            return
        records = payload.get("records") or []
        for raw in records:
            try:
                rec = _BucketRecord.from_dict(raw)
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug("bucket_pnl_state skip bad record %r: %s", raw, exc)
                continue
            key = BucketKey(
                cluster=normalize_cluster(rec.cluster),
                interval=normalize_interval(rec.interval),
                score_bucket=str(rec.score_bucket).strip().lower(),
            )
            self._buckets.setdefault(key, deque()).append(rec)
        self._last_loaded_at = datetime.now()
        # 注：load 后不主动 purge（避免用 wall clock 把测试 / 历史回放数据误清）；
        # caller 需要时显式调 purge_expired(now=...)

    def save(self) -> Path | None:
        if not self.state_path:
            return None
        p = Path(self.state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        records: list[dict] = []
        for key, dq in self._buckets.items():
            for rec in dq:
                d = rec.to_dict()
                # 保险：补 key（虽然 rec 已有）
                d["cluster"] = key.cluster
                d["interval"] = key.interval
                d["score_bucket"] = key.score_bucket
                records.append(d)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "window_days": int(self.window_days),
            "records": records,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p

    # ── core API ────────────────────────────────────────────────────

    def on_trade(self, trade: dict) -> None:
        """收一笔已闭合 trade。

        必填字段：``cluster``, ``interval``, ``score_bucket``, ``dt``, ``net_pnl``, ``portfolio_equity``。
        ``portfolio_equity <= 0`` 时跳过（bp 分母不可用）。
        """
        try:
            cluster = normalize_cluster(trade.get("cluster"))
            interval = normalize_interval(trade.get("interval"))
            bucket = str(trade.get("score_bucket", "")).strip().lower()
            if not cluster or not interval or not bucket:
                return
            dt = pd.Timestamp(trade.get("dt", pd.Timestamp.now()))
            pnl = float(trade.get("net_pnl", 0.0))
            equity = float(trade.get("portfolio_equity", 0.0))
        except (TypeError, ValueError) as exc:
            logger.debug("on_trade skip bad trade %r: %s", trade, exc)
            return
        if equity <= 0.0:
            return
        pnl_bp = pnl / equity * 10_000.0
        key = BucketKey(cluster=cluster, interval=interval, score_bucket=bucket)
        rec = _BucketRecord(
            dt=dt,
            pnl_bp=pnl_bp,
            cluster=cluster,
            interval=interval,
            score_bucket=bucket,
            symbol=str(trade.get("symbol", "")).strip().upper(),
            raw_pnl=pnl,
            raw_equity=equity,
        )
        self._buckets.setdefault(key, deque()).append(rec)
        # 注：on_trade 不主动 evict；evict 在 rolling_pnl_bp / trade_count / purge_expired
        # 这些 query API 里按调用方传入的 now 处理，避免用"新 trade 的 dt"误清更早的旧 trade。

    def rolling_pnl_bp(self, key: BucketKey, *, now: pd.Timestamp | None = None) -> float:
        """指定桶在 window_days 内累计 bp PnL。无数据返回 0.0。"""
        self._evict_for(key, now=now)
        dq = self._buckets.get(key)
        if not dq:
            return 0.0
        return float(sum(rec.pnl_bp for rec in dq))

    def trade_count(self, key: BucketKey, *, now: pd.Timestamp | None = None) -> int:
        self._evict_for(key, now=now)
        dq = self._buckets.get(key)
        return int(len(dq)) if dq else 0

    def purge_expired(self, *, now: pd.Timestamp | None = None) -> int:
        """清理所有桶里超窗的 trade，返回清掉的总条数。"""
        n_purged = 0
        for key in list(self._buckets.keys()):
            before = len(self._buckets[key])
            self._evict_for(key, now=now)
            after = len(self._buckets[key])
            n_purged += before - after
            if after == 0:
                del self._buckets[key]
        return n_purged

    def known_buckets(self) -> list[BucketKey]:
        return list(self._buckets.keys())

    def replay(self, trades: Iterable[dict]) -> int:
        """批量回放（OOT 用：trade_details.csv → tracker 状态）。"""
        n = 0
        for t in trades:
            self.on_trade(t)
            n += 1
        return n

    # ── internals ───────────────────────────────────────────────────

    def _evict_for(self, key: BucketKey, *, now: pd.Timestamp | None = None) -> None:
        dq = self._buckets.get(key)
        if not dq:
            return
        ref = pd.Timestamp(now) if now is not None else pd.Timestamp(dq[-1].dt)
        cutoff = ref - pd.Timedelta(days=int(self.window_days))
        while dq and pd.Timestamp(dq[0].dt) < cutoff:
            dq.popleft()


__all__ = [
    "BucketKey",
    "BucketPnlTracker",
    "SCHEMA_VERSION",
    "classify_score_bucket",
]
