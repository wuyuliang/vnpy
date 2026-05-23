"""vnpy ``CtaTemplate`` wrapper for baseline setup detection (P0-1).

为什么需要这个 wrapper
----------------------
研究阶段：[strategy/baseline_setup_detection.py](../baseline_setup_detection.py)
按 candidate dataframe 一次性产出所有 setup 候选。

sim/live 阶段：vnpy 的 ``CtaTemplate`` 是流式 / 事件驱动的：``on_bar`` 一根根来，
``on_trade`` 是回报。需要一层 adapter 把 baseline 算法包装成 ``CtaTemplate`` 子类。

设计原则
--------
- **零侵入**：不修改 ``baseline_setup_detection.py``，只新增 adapter
- **测试友好**：``CtaTemplate`` 在没装 vnpy 时用一个 stub base class（仅测试用）
- **复用风控**：sim_runner 的 ``risk_guard`` / ``kill_switch`` 经由 ``send_order_with_guard`` 串联
- **状态最小化**：adapter 内部只保存最近 N 根 bar，不重复造特征轮子；特征 / 模型由
  [live/online_feature.py](../../live/online_feature.py) 注入

接口契约
--------
``BaselineSetupVnpyStrategy.on_bar(bar)`` → 调用 baseline_setup_detection →
``self.buy() / sell() / short() / cover()`` 发单（vnpy 自动经 gateway）。

P0-1 MVP：本文件实现 **donchian_breakout** 一种 signal_type 的 wrapper；其他
signal_type 可按同一 pattern 复制粘贴。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from cta.strategy.vnpy_adapters.signal_evaluators import (
    EvaluatorConfig,
    SIGNAL_EVALUATORS,
    dispatch_signal,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CtaTemplate 抽象基类：装了 vnpy 用真的，没装用 stub（仅测试）
# ---------------------------------------------------------------------------

try:
    from vnpy_ctastrategy import CtaTemplate as _RealCtaTemplate  # type: ignore
    _HAS_VNPY = True
except Exception:  # noqa: BLE001
    _RealCtaTemplate = None  # type: ignore
    _HAS_VNPY = False


class _StubCtaTemplate:
    """vnpy 未安装时的 stub base class，仅用于单测。"""
    parameters: list = []
    variables: list = []

    def __init__(self, cta_engine: Any = None, strategy_name: str = "",
                 vt_symbol: str = "", setting: dict | None = None) -> None:
        self.cta_engine = cta_engine
        self.strategy_name = strategy_name
        self.vt_symbol = vt_symbol
        self.setting = setting or {}
        self.pos: int = 0
        self.trading: bool = False
        self.inited: bool = False
        self._sent_orders: list[dict[str, Any]] = []

    def write_log(self, msg: str) -> None:
        logger.info("[%s] %s", self.strategy_name, msg)

    def buy(self, price: float, volume: float, stop: bool = False) -> list:
        self._sent_orders.append({"side": "long_open", "price": price, "volume": volume, "stop": stop})
        return ["mock_order_id"]

    def sell(self, price: float, volume: float, stop: bool = False) -> list:
        self._sent_orders.append({"side": "long_close", "price": price, "volume": volume, "stop": stop})
        return ["mock_order_id"]

    def short(self, price: float, volume: float, stop: bool = False) -> list:
        self._sent_orders.append({"side": "short_open", "price": price, "volume": volume, "stop": stop})
        return ["mock_order_id"]

    def cover(self, price: float, volume: float, stop: bool = False) -> list:
        self._sent_orders.append({"side": "short_close", "price": price, "volume": volume, "stop": stop})
        return ["mock_order_id"]

    def cancel_all(self) -> None:
        pass

    def put_event(self) -> None:
        pass


# 派生时用真 / stub
_BaseCtaTemplate: type = _RealCtaTemplate if _HAS_VNPY else _StubCtaTemplate


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BaselineStrategyConfig:
    """单实例配置；通过 signal_type 路由到 SIGNAL_EVALUATORS。"""
    signal_type: str = "donchian_breakout"
    donchian_window: int = 20
    atr_window: int = 14
    fixed_volume: int = 1
    stop_loss_pct: float = 0.01
    enable: bool = True
    max_buffer_bars: int = 100  # adapter 内部维护的 bar 缓冲长度
    # 高级 evaluator 参数（不在此 cfg 默认值，全部在 EvaluatorConfig 内）
    evaluator_overrides: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if str(self.signal_type) not in SIGNAL_EVALUATORS:
            raise ValueError(
                f"unknown signal_type={self.signal_type!r}; supported={sorted(SIGNAL_EVALUATORS)}"
            )
        if int(self.donchian_window) <= 1:
            raise ValueError("donchian_window must be > 1")
        if int(self.atr_window) <= 0:
            raise ValueError("atr_window must be > 0")
        if int(self.fixed_volume) <= 0:
            raise ValueError("fixed_volume must be > 0")
        if float(self.stop_loss_pct) <= 0:
            raise ValueError("stop_loss_pct must be > 0")

    def to_evaluator_cfg(self) -> EvaluatorConfig:
        """从 strategy cfg 派生 evaluator cfg；evaluator_overrides 覆盖默认值。"""
        kwargs: dict[str, Any] = {
            "donchian_window": self.donchian_window,
            "atr_window": self.atr_window,
        }
        kwargs.update(dict(self.evaluator_overrides or {}))
        return EvaluatorConfig(**kwargs)


# ---------------------------------------------------------------------------
# 策略主类
# ---------------------------------------------------------------------------

class BaselineSetupVnpyStrategy(_BaseCtaTemplate):
    """把 baseline setup 包装成 vnpy CtaTemplate。

    一个实例对应一个 (vt_symbol, signal_type) 组合。多 signal_type 时上层注册多个实例。

    使用：
        cfg = BaselineStrategyConfig(signal_type="donchian_breakout", ...)
        strategy = BaselineSetupVnpyStrategy(
            cta_engine, "donchian_RB0", "RB2501.SHFE",
            setting={"baseline_cfg": cfg},
        )
        # 后续 cta_engine 把 on_tick / on_bar 喂进来
    """

    parameters = ["signal_type", "donchian_window", "atr_window", "fixed_volume", "stop_loss_pct"]
    variables = ["pos", "last_signal_bar_idx"]

    def __init__(
        self,
        cta_engine: Any = None,
        strategy_name: str = "",
        vt_symbol: str = "",
        setting: dict | None = None,
    ) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        cfg = (setting or {}).get("baseline_cfg") or BaselineStrategyConfig()
        if not isinstance(cfg, BaselineStrategyConfig):
            raise TypeError("setting['baseline_cfg'] must be BaselineStrategyConfig")
        self.cfg: BaselineStrategyConfig = cfg
        # 独立的下单审计 trail，测试 / 复盘 / parity 都可用（与 base class 状态解耦）
        self._sent_orders: list[dict[str, Any]] = []
        # 让 super 的某些字段在 stub / 真 vnpy 上都成立
        if not hasattr(self, "pos") or self.pos is None:
            self.pos = 0
        if not hasattr(self, "trading"):
            self.trading = False
        if not hasattr(self, "inited"):
            self.inited = False
        # vnpy CtaTemplate 期望把 parameter 字段挂到 self 上
        self.signal_type = cfg.signal_type
        self.donchian_window = cfg.donchian_window
        self.atr_window = cfg.atr_window
        self.fixed_volume = cfg.fixed_volume
        self.stop_loss_pct = cfg.stop_loss_pct
        self.last_signal_bar_idx: int = -1
        self._bar_buffer: list[Any] = []
        self._stop_price: float | None = None
        # 可选 hook：让 sim_runner 注入 risk_guard / kill_switch / model_filter
        self._extra_filter: Callable[[dict[str, Any]], bool] | None = (setting or {}).get(
            "extra_filter"
        )

    # --- vnpy lifecycle hooks（绕开 base.write_log，直接打 logger，避免 cta_engine=None 崩） ---

    def _safe_log(self, msg: str) -> None:
        if self.cta_engine is not None and hasattr(super(), "write_log"):
            try:
                super().write_log(msg)
                return
            except Exception:  # noqa: BLE001
                pass
        logger.info("[%s] %s", self.strategy_name, msg)

    def on_init(self) -> None:
        self._safe_log(f"strategy init: signal_type={self.signal_type}")
        self.inited = True

    def on_start(self) -> None:
        self._safe_log("strategy start")
        self.trading = True

    def on_stop(self) -> None:
        self._safe_log("strategy stop")
        self.trading = False

    def on_tick(self, tick: Any) -> None:  # noqa: ARG002 - vnpy interface
        # 本 MVP 只走 bar 级别，不在 tick 上反应
        pass

    # --- 核心：每根 bar 评估 ---

    def on_bar(self, bar: Any) -> None:
        """每根 bar 评估 donchian_breakout 信号 + 风控止损。"""
        if not self.cfg.enable:
            return
        self._bar_buffer.append(bar)
        if len(self._bar_buffer) > self.cfg.max_buffer_bars:
            self._bar_buffer.pop(0)

        # 持仓阶段：先检查止损
        if self.pos != 0 and self._stop_price is not None:
            close = float(getattr(bar, "close_price", 0.0))
            if self.pos > 0 and close <= self._stop_price:
                self._close_long(price=close, reason="hard_stop")
                return
            if self.pos < 0 and close >= self._stop_price:
                self._close_short(price=close, reason="hard_stop")
                return

        # 信号检测：按 signal_type dispatch
        decision = dispatch_signal(
            self.cfg.signal_type, self._bar_buffer, self.cfg.to_evaluator_cfg(),
        )
        if decision is None:
            return

        # extra_filter（model_filter / risk_guard）
        if self._extra_filter is not None and not self._extra_filter(decision):
            self._safe_log(f"order blocked by extra_filter: {decision}")
            return

        # 下单
        if decision["side"] == "long_open" and self.pos == 0:
            self._open_long(price=decision["price"])
        elif decision["side"] == "short_open" and self.pos == 0:
            self._open_short(price=decision["price"])

    def on_trade(self, trade: Any) -> None:  # noqa: ARG002 - 仅记录
        self._safe_log(f"trade: {trade}")
        if hasattr(self, "put_event") and self.cta_engine is not None:
            try:
                self.put_event()
            except Exception:  # noqa: BLE001
                pass

    def on_order(self, order: Any) -> None:  # noqa: ARG002
        pass

    def on_stop_order(self, stop_order: Any) -> None:  # noqa: ARG002
        pass

    # --- 下单 helper：先 append audit trail，再视情况调 vnpy gateway ---

    def _record_and_send(
        self,
        *,
        side: str,
        price: float,
        volume: float,
        reason: str = "",
    ) -> bool:
        """记录下单审计 + 真实 gateway 调用（仅在 cta_engine 可用时）。

        返回 True 表示发单成功（或离线/测试场景 by-design 不发单但视为成功），
        False 表示真实 gateway 调用抛异常 → 调用方**必须不更新本地仓位**，避免
        与账户实际持仓漂移（codex P1-C 修复）。
        """
        self._sent_orders.append({
            "side": side, "price": float(price),
            "volume": float(volume), "reason": reason,
        })
        if self.cta_engine is None:
            # 测试 / 离线场景：不真正发单到 vnpy，视为"成功"以便走完单测路径
            return True
        try:
            if side == "long_open":
                self.buy(price=price, volume=float(volume))
            elif side == "long_close":
                self.sell(price=price, volume=float(volume))
            elif side == "short_open":
                self.short(price=price, volume=float(volume))
            elif side == "short_close":
                self.cover(price=price, volume=float(volume))
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "vnpy gateway order failed: side=%s price=%s vol=%s; "
                "NOT updating local pos to avoid drift",
                side, price, volume,
            )
            return False

    def _open_long(self, price: float) -> None:
        ok = self._record_and_send(side="long_open", price=price, volume=self.cfg.fixed_volume)
        if not ok:
            return  # P1-C: 下单失败 → 不调整 self.pos
        self.pos += int(self.cfg.fixed_volume)
        self._stop_price = price * (1.0 - float(self.cfg.stop_loss_pct))
        logger.info(
            "[%s] open long @ %.2f vol=%d stop=%.2f",
            self.strategy_name, price, self.cfg.fixed_volume, self._stop_price,
        )

    def _open_short(self, price: float) -> None:
        ok = self._record_and_send(side="short_open", price=price, volume=self.cfg.fixed_volume)
        if not ok:
            return
        self.pos -= int(self.cfg.fixed_volume)
        self._stop_price = price * (1.0 + float(self.cfg.stop_loss_pct))
        logger.info(
            "[%s] open short @ %.2f vol=%d stop=%.2f",
            self.strategy_name, price, self.cfg.fixed_volume, self._stop_price,
        )

    def _close_long(self, price: float, reason: str) -> None:
        ok = self._record_and_send(side="long_close", price=price, volume=abs(self.pos), reason=reason)
        if not ok:
            return  # P1-C: 不清 pos / stop_price
        logger.info("[%s] close long @ %.2f reason=%s", self.strategy_name, price, reason)
        self.pos = 0
        self._stop_price = None

    def _close_short(self, price: float, reason: str) -> None:
        ok = self._record_and_send(side="short_close", price=price, volume=abs(self.pos), reason=reason)
        if not ok:
            return
        logger.info("[%s] close short @ %.2f reason=%s", self.strategy_name, price, reason)
        self.pos = 0
        self._stop_price = None


__all__ = [
    "BaselineSetupVnpyStrategy",
    "BaselineStrategyConfig",
]
