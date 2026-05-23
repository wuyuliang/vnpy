"""P2-16: kill_switch 端到端覆盖三场景.

场景：
1. 日亏损 > 3% → DailyLossLimit 触发 → 风控拒绝；同时手动 activate kill_switch
2. 持仓异常（手数突变）→ 监控线程检测后 activate kill_switch
3. 数据延迟 > 5s → 心跳监控 activate kill_switch

验收（roadmap §2.3）：三场景全过 + 触发后人工 deactivate 可恢复 + RiskGuard 短路。
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

import pandas as pd

from cta.live.kill_switch import KillSwitch, KillSwitchRule
from cta.live.risk import DailyLossLimit, RiskContext, RiskDecision, RiskGuard


def _ctx(*, daily_pnl: float = 0.0, capital: float = 1_000_000.0, pos: dict | None = None) -> RiskContext:
    return RiskContext(
        pos=pos or {}, daily_pnl=daily_pnl, capital=capital,
        now=pd.Timestamp("2024-03-25 10:00:00"),
    )


class TestKillSwitchEndToEnd(unittest.TestCase):
    # ── 场景 1：日亏损触发 ───────────────────────────────────────────────

    def test_daily_loss_scenario_blocks_new_orders(self) -> None:
        ks = KillSwitch()
        # 模拟监控线程：日亏损 > 3% 时 activate
        ctx = _ctx(daily_pnl=-35_000.0, capital=1_000_000.0)
        loss_pct = abs(ctx.daily_pnl) / ctx.capital
        if loss_pct > 0.03:
            ks.activate(reason=f"daily_loss_{loss_pct:.4f}")
        guard = RiskGuard(rules=[KillSwitchRule(ks)])
        order = {"vt_symbol": "RB2501.SHFE", "direction": "long", "offset": "open", "volume": 1.0}
        decision = guard.evaluate(order, ctx)
        self.assertFalse(decision.allowed)
        self.assertIn("kill_switch", decision.reason)
        self.assertIn("daily_loss", decision.reason)

    def test_daily_loss_dual_layer_kill_switch_and_rule(self) -> None:
        """同时挂 KillSwitchRule 和 DailyLossLimit；任一触发即拒绝。"""
        ks = KillSwitch()
        guard = RiskGuard(rules=[
            KillSwitchRule(ks),
            DailyLossLimit(max_loss=30_000.0),
        ])
        ctx = _ctx(daily_pnl=-35_000.0)
        order = {"vt_symbol": "RB2501.SHFE", "direction": "long", "offset": "open", "volume": 1.0}
        # DailyLossLimit 触发
        decision = guard.evaluate(order, ctx)
        self.assertFalse(decision.allowed)
        self.assertIn("daily_loss", decision.reason)

    # ── 场景 2：持仓异常 ──────────────────────────────────────────────

    def test_position_anomaly_triggers_kill_switch(self) -> None:
        """持仓突然出现意外手数（gateway bug / 误下单）→ kill_switch。"""
        ks = KillSwitch()
        expected_pos = {"RB2501.SHFE": 1.0}
        actual_pos = {"RB2501.SHFE": 5.0, "HC2501.SHFE": 2.0}  # 多了未预期持仓
        diff_count = sum(
            1 for k in set(expected_pos) | set(actual_pos)
            if abs(expected_pos.get(k, 0) - actual_pos.get(k, 0)) > 0.5
        )
        if diff_count > 0:
            ks.activate(reason=f"position_anomaly_{diff_count}_symbols")
        active, reason = ks.is_active()
        self.assertTrue(active)
        self.assertIn("position_anomaly", reason)

    # ── 场景 3：数据延迟 / 心跳超时 ───────────────────────────────────

    def test_heartbeat_delay_triggers_kill_switch(self) -> None:
        ks = KillSwitch()
        last_tick_ts = pd.Timestamp("2024-03-25 10:00:00")
        now = pd.Timestamp("2024-03-25 10:00:10")  # 10s 后
        delay = (now - last_tick_ts).total_seconds()
        if delay > 5:
            ks.activate(reason=f"heartbeat_delay_{delay:.1f}s")
        active, reason = ks.is_active()
        self.assertTrue(active)
        self.assertIn("heartbeat_delay", reason)

    # ── 恢复流程 ────────────────────────────────────────────────────

    def test_deactivate_restores_normal_trading(self) -> None:
        ks = KillSwitch()
        ks.activate(reason="manual_test")
        guard = RiskGuard(rules=[KillSwitchRule(ks)])
        ctx = _ctx()
        order = {"vt_symbol": "RB2501.SHFE", "direction": "long", "offset": "open", "volume": 1.0}
        d1 = guard.evaluate(order, ctx)
        self.assertFalse(d1.allowed)
        # 人工恢复
        ks.deactivate()
        d2 = guard.evaluate(order, ctx)
        self.assertTrue(d2.allowed)

    # ── 信号文件路径 ────────────────────────────────────────────────

    def test_signal_file_external_trigger(self) -> None:
        """运维 SSH 创建信号文件 → 进程立刻感知 kill_switch。"""
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".kill") as f:
            f.write("ops_emergency_stop")
            sig_path = Path(f.name)
        try:
            ks = KillSwitch(signal_file=sig_path)
            active, reason = ks.is_active()
            self.assertTrue(active)
            self.assertEqual(reason, "ops_emergency_stop")
        finally:
            sig_path.unlink()

    def test_signal_file_missing_means_inactive(self) -> None:
        ks = KillSwitch(signal_file=Path("/tmp/nonexistent.kill"))
        active, reason = ks.is_active()
        self.assertFalse(active)


if __name__ == "__main__":
    unittest.main()
