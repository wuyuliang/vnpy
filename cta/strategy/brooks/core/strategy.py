"""BrooksV3Core:组合 HTF→MTF→LTF + 评分门控 + 风控 + 止损 + 日志。

对 vnpy 无依赖,由 backtest/engine.py 与 online/live_strategy.py 分别包装。

使用方式:
    core = BrooksV3Core(vt_symbol='RB0.SHFE', params=params,
                         adapter=FeatureAdapter('offline'),
                         score_gate=ScoreGate.load(...),
                         portfolio=PortfolioRiskManager(),
                         trade_logger=TradeLogger(output_path=...),
                         contract_size=10)
    for i, bar in enumerate(ltf_bars):
        action = core.on_bar(i, bar)   # 返回 ('BUY'|'SELL'|'HOLD', qty, price)
        # 外部据此下单
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from cta.strategy.brooks.config.params import BrooksV3Params
from cta.strategy.brooks.core.features.adapter import FeatureAdapter
from cta.strategy.brooks.core.model.score_gate import ScoreGate
from cta.strategy.brooks.core.risk import (
    PortfolioRiskManager,
    StopEngine,
    calc_position_size,
)
from cta.strategy.brooks.core.risk.stops import ExitReason, StopState
from cta.strategy.brooks.core.signal import (
    detect_htf_bias,
    detect_ltf_entry,
    detect_mtf_setup,
)
from cta.strategy.brooks.core.trade_log import TradeLogger, TradeRecord, dumps_features

logger = logging.getLogger(__name__)

Action = Literal["BUY_OPEN", "SELL_CLOSE", "HOLD"]


@dataclass
class CoreDecision:
    action: Action
    qty: int = 0
    price: float = 0.0
    reason: str = ""


@dataclass
class BrooksV3Core:
    vt_symbol: str
    params: BrooksV3Params
    adapter: FeatureAdapter
    score_gate: ScoreGate
    portfolio: PortfolioRiskManager
    trade_logger: TradeLogger
    contract_size: float
    price_tick: float = 1.0
    initial_capital: float = 1_000_000.0

    # 运行时状态
    stop_engine: StopEngine = field(init=False)
    position_qty: int = 0
    position_state: StopState | None = None
    current_trade: TradeRecord | None = None
    equity: float = 0.0                    # 已实现 + 浮动
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        r = self.params.risk
        self.stop_engine = StopEngine(
            stop_atr_mult=r.stop_atr_mult,
            trailing_atr_mult=r.trailing_atr_mult,
            fail_exit_bars=r.fail_exit_bars,
            max_holding_bars=r.max_holding_bars,
        )
        self.equity = self.initial_capital
        self.realized_pnl = 0.0

    # ---------- public API ----------
    def on_bar(self, idx: int, bar: pd.Series) -> CoreDecision:
        """bar 必须含 ltf 特征(由 FeatureAdapter.get_range 加载的行)。"""
        ts = pd.Timestamp(bar["datetime"])

        # 更新浮动 equity
        self._update_equity(bar)
        self.portfolio.update(self.equity, ts=str(ts))

        # 有持仓 → 检查退出
        if self.position_qty > 0 and self.position_state is not None:
            high = float(bar["high"]); low = float(bar["low"]); close = float(bar["close"])
            reason, px = self.stop_engine.on_bar(self.position_state, high, low, close)
            if reason != ExitReason.NONE:
                return self._close_position(ts, px, reason)
            return CoreDecision(action="HOLD", reason="in_position")

        # 无持仓 → 检查入场
        return self._try_open(idx, bar, ts)

    def finalize(self) -> None:
        self.trade_logger.write_parquet()

    # ---------- internals ----------
    def _update_equity(self, bar: pd.Series) -> None:
        close = float(bar["close"])
        unrealized = 0.0
        if self.position_qty > 0 and self.position_state is not None:
            unrealized = (close - self.position_state.entry_price) * self.position_qty * self.contract_size
        self.equity = self.initial_capital + self.realized_pnl + unrealized

    def _try_open(self, idx: int, bar: pd.Series, ts: pd.Timestamp) -> CoreDecision:
        htf_feat = self.adapter.get(self.vt_symbol, self.params.intervals.htf, ts)
        mtf_feat = self.adapter.get(self.vt_symbol, self.params.intervals.mtf, ts)

        hb = detect_htf_bias(htf_feat, self.params.signal.htf)
        if hb.direction != 1:
            return CoreDecision("HOLD", reason=f"htf_{hb.reason}")
        ms = detect_mtf_setup(mtf_feat, hb.direction, self.params.signal.mtf)
        if not ms.is_valid:
            return CoreDecision("HOLD", reason=f"mtf_{ms.reason}")
        le = detect_ltf_entry(bar, ms.direction, self.params.signal.ltf, self.price_tick)
        if not le.should_enter:
            return CoreDecision("HOLD", reason=f"ltf_{le.reason}")

        # 特征向量(给模型 + 日志)
        feats = self._collect_features(bar, hb, ms)
        prob, accept = self.score_gate.score(feats)
        if not accept:
            logger.debug("rejected_by_model prob=%.3f", prob)
            return CoreDecision("HOLD", reason=f"rejected_by_model_p={prob:.3f}")

        # ATR(从 bar 特征获取;落盘特征通常含 atr_14)
        atr = float(bar.get(f"atr_{self.params.risk.atr_window}",
                            bar.get("atr_14", 0.0)) or 0.0)
        if atr <= 0:
            return CoreDecision("HOLD", reason="no_atr")

        stop_distance = le.trigger_price - le.stop_price
        if stop_distance <= 0:
            return CoreDecision("HOLD", reason="bad_stop_distance")

        qty = calc_position_size(
            equity=self.equity,
            stop_distance=stop_distance,
            contract_size=self.contract_size,
            risk_pct=self.params.risk.per_trade_risk_pct,
            leverage_mult=self.portfolio.current_leverage,
        )
        if qty <= 0:
            return CoreDecision("HOLD", reason="qty_zero")

        # 开仓
        self.position_qty = qty
        self.position_state = self.stop_engine.init_state(
            entry_price=le.trigger_price, entry_idx=idx, atr=atr, direction=1,
        )
        self.current_trade = TradeRecord(
            vt_symbol=self.vt_symbol,
            open_ts=ts, direction=1, qty=qty,
            entry_price=le.trigger_price,
            initial_stop=self.position_state.initial_stop,
            setup_type=ms.setup_type,
            htf_direction=hb.direction,
            htf_trend_strength=hb.strength,
            mtf_pullback_depth=ms.pullback_depth,
            model_prob=prob,
            model_accepted=accept,
            leverage_mult=self.portfolio.current_leverage,
            equity_at_open=self.equity,
            features_json=dumps_features(feats),
        )
        logger.info("OPEN %s @%s qty=%d px=%.2f stop=%.2f prob=%.3f setup=%s",
                    self.vt_symbol, ts, qty, le.trigger_price,
                    self.position_state.initial_stop, prob, ms.setup_type)
        return CoreDecision("BUY_OPEN", qty=qty, price=le.trigger_price,
                            reason=f"open_{ms.setup_type}")

    def _close_position(self, ts: pd.Timestamp, price: float,
                        reason: ExitReason) -> CoreDecision:
        assert self.position_state is not None
        assert self.current_trade is not None
        st = self.position_state
        qty = self.position_qty
        trade = self.current_trade
        # 结算
        trade.final_stop = st.trailing_stop
        trade.mfe = st.peak_price - st.entry_price
        trade.mae = 0.0  # 简化:on_bar 里没逐 bar 记 low 最低,后续可接
        trade.holding_bars = st.bars_held
        trade.finalize(close_ts=ts, exit_price=price, exit_reason=reason.value,
                       contract_size=self.contract_size)
        self.realized_pnl += trade.pnl
        self.trade_logger.add(trade)
        logger.info("CLOSE %s @%s px=%.2f pnl=%.2f reason=%s",
                    self.vt_symbol, ts, price, trade.pnl, reason.value)
        # 清状态
        self.position_qty = 0
        self.position_state = None
        self.current_trade = None
        return CoreDecision("SELL_CLOSE", qty=qty, price=price, reason=reason.value)

    def _collect_features(self, bar: pd.Series, hb, ms) -> dict:
        cols = self.score_gate.feature_cols
        if cols is None:
            # 未加载模型时退化到全 pa_* 列
            cols = [c for c in bar.index if str(c).startswith("pa_")]
        feats: dict = {}
        for c in cols:
            if c.startswith("ctx_"):
                continue
            feats[c] = float(bar.get(c, 0.0)) if bar.get(c) is not None else 0.0
        feats["ctx_htf_trend_strength"] = hb.strength
        feats["ctx_mtf_pullback_depth"] = ms.pullback_depth
        feats["ctx_mtf_h123"] = float({"h1": 1, "h2": 2, "h3": 3}.get(ms.setup_type, 0))
        return feats


__all__ = ["BrooksV3Core", "CoreDecision"]
