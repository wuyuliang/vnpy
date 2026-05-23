"""Sim soak gate + 实盘灰度 + 小额验证 + 回退预案统一入口 (P3-23/24/25/27).

roadmap §5.2 实盘灰度启动门槛、§5.3 全 universe 实盘门槛都需要：
1. 累积 N 天的 parity report，判定门槛是否通过
2. 灰度阶段记录（1 symbol → 1 cluster → 全 universe）
3. 异常事件计数（kill_switch / reject / 凭据问题）
4. 回退预案（cluster_registry 回滚 + 数据快照）

本模块提供：
- ``SoakReport``：每日 parity 累积 → 30 天 soak 门槛判定
- ``RolloutPlan``：灰度阶段 + 退出条件
- ``RollbackProcedure``：回退步骤记录 + checklist
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Sim soak gate (P3-23) ───────────────────────────────────────────────


@dataclass(frozen=True)
class DailyParitySnapshot:
    """一天的 parity 测量结果。"""
    trade_date: date
    n_sim_trades: int
    n_oot_trades: int
    matched: int
    mismatched: int
    only_in_sim: int
    only_in_oot: int
    cumulative_pnl_diff_bp: float

    @property
    def is_pass(self) -> bool:
        return abs(self.cumulative_pnl_diff_bp) <= 10.0 and self.mismatched <= 1


@dataclass
class SoakReport:
    """30-day sim soak 累积报告。"""
    daily_snapshots: list[DailyParitySnapshot] = field(default_factory=list)
    required_days: int = 30
    max_pnl_diff_pct: float = 0.05
    allowed_failed_days: int = 2

    def append(self, snap: DailyParitySnapshot) -> None:
        self.daily_snapshots.append(snap)

    @property
    def days_passed(self) -> int:
        return sum(1 for s in self.daily_snapshots if s.is_pass)

    @property
    def days_failed(self) -> int:
        return sum(1 for s in self.daily_snapshots if not s.is_pass)

    @property
    def cumulative_pnl_diff_pct(self) -> float:
        if not self.daily_snapshots:
            return 0.0
        # 取最新一天的累积值
        return abs(self.daily_snapshots[-1].cumulative_pnl_diff_bp) / 10_000.0

    def gate_passes(self) -> tuple[bool, str]:
        """返回 (passed, reason)。"""
        if len(self.daily_snapshots) < self.required_days:
            return False, f"insufficient_days: have={len(self.daily_snapshots)} need={self.required_days}"
        if self.days_failed > self.allowed_failed_days:
            return False, f"too_many_failed_days: failed={self.days_failed} max_allowed={self.allowed_failed_days}"
        if self.cumulative_pnl_diff_pct > self.max_pnl_diff_pct:
            return False, f"pnl_drift_too_large: {self.cumulative_pnl_diff_pct:.4%} > {self.max_pnl_diff_pct:.2%}"
        return True, f"passed: {self.days_passed}/{len(self.daily_snapshots)} days OK"

    def save_to_json(self, path: Path | str) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "required_days": self.required_days,
                    "max_pnl_diff_pct": self.max_pnl_diff_pct,
                    "allowed_failed_days": self.allowed_failed_days,
                    "days_passed": self.days_passed,
                    "days_failed": self.days_failed,
                    "cumulative_pnl_diff_pct": self.cumulative_pnl_diff_pct,
                    "snapshots": [
                        {**asdict(s), "trade_date": s.trade_date.isoformat()}
                        for s in self.daily_snapshots
                    ],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )


# ─── Rollout plan (P3-24/25) ─────────────────────────────────────────────


@dataclass(frozen=True)
class RolloutStage:
    name: str
    symbols: tuple[str, ...]
    min_days: int
    max_daily_loss_pct: float
    capital_at_risk: float

    def is_complete(self, *, days_run: int, max_daily_loss_seen: float) -> tuple[bool, str]:
        if days_run < self.min_days:
            return False, f"need {self.min_days} days, have {days_run}"
        if max_daily_loss_seen > self.max_daily_loss_pct:
            return False, f"daily_loss {max_daily_loss_seen:.2%} > {self.max_daily_loss_pct:.2%}"
        return True, "ok"


# 推荐默认灰度阶段（roadmap §5.2-§5.3）
DEFAULT_ROLLOUT_STAGES: tuple[RolloutStage, ...] = (
    RolloutStage(
        name="single_symbol",
        symbols=("RB0",),
        min_days=5,
        max_daily_loss_pct=0.01,
        capital_at_risk=50_000.0,
    ),
    RolloutStage(
        name="single_cluster",
        symbols=("RB0", "HC0", "I0", "J0", "JM0"),  # black cluster
        min_days=14,
        max_daily_loss_pct=0.015,
        capital_at_risk=200_000.0,
    ),
    RolloutStage(
        name="full_universe",
        symbols=(),  # 全量
        min_days=30,
        max_daily_loss_pct=0.02,
        capital_at_risk=1_000_000.0,
    ),
)


# ─── Rollback plan (P3-27) ───────────────────────────────────────────────


@dataclass(frozen=True)
class RollbackTrigger:
    name: str
    threshold_description: str


DEFAULT_ROLLBACK_TRIGGERS: tuple[RollbackTrigger, ...] = (
    RollbackTrigger("daily_pnl_critical", "单日 PnL < -5% capital"),
    RollbackTrigger("max_drawdown_breach", "累计回撤 > 10% capital"),
    RollbackTrigger("model_parity_drift", "5 日累计 parity diff > 100bp"),
    RollbackTrigger("kill_switch_repeated", "24h 内 kill_switch 触发 ≥ 3 次"),
    RollbackTrigger("gateway_disconnect_loop", "supervisor.reconnect_count > 20 / day"),
    RollbackTrigger("reject_rate_high", "订单 reject rate > 1%"),
)


@dataclass(frozen=True)
class RollbackChecklist:
    """回退执行步骤（人工操作清单）。"""
    steps: tuple[str, ...] = (
        "1. 全局 kill_switch.activate(reason='manual_rollback')",
        "2. 等所有 in-flight orders 处理完（cancel pending）",
        "3. snapshot 当前 cluster_registry.json + 模型 joblib 备份",
        "4. 回滚 cluster_registry.json 到上一个稳定版本",
        "5. 触发模型热加载：kill -HUP <pid>（或重启进程）",
        "6. 验证模型版本号 + 跑 1 笔小额测试单",
        "7. 写 incident 记录到 cta/report/change_log.md",
        "8. 通知运维 / 风控 / 量化团队（钉钉 / 邮件）",
    )

    def render(self) -> str:
        return "\n".join(self.steps)


__all__ = [
    "DEFAULT_ROLLBACK_TRIGGERS",
    "DEFAULT_ROLLOUT_STAGES",
    "DailyParitySnapshot",
    "RollbackChecklist",
    "RollbackTrigger",
    "RolloutStage",
    "SoakReport",
]
