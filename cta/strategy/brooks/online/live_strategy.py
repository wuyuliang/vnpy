"""v3 在线策略:CtaTemplate 桥接 BrooksV3Core。

职责:
- 通过 BarGenerator 把 tick/1m 汇聚成策略周期(params.intervals.ltf)
- on_bar 时拉 ArrayManager → 取 pa_* 特征(通过 FeatureGenerator 增量)
- 调用 BrooksV3Core.on_bar → 按 CoreDecision 下单

注意:
- 本模块只实现策略壳,不启动 vnpy 引擎;启动脚本由使用者结合
  vnpy_ctastrategy CtaEngine / vnpy_ctabacktester 各自 cli 发起
- 仅保留多头逻辑(与 BrooksV3Core 一致)
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from vnpy.trader.object import BarData, TickData
from vnpy.trader.utility import ArrayManager, BarGenerator
from vnpy_ctastrategy import CtaTemplate

from cta.strategy.brooks.config.params import load_params
from cta.strategy.brooks.config.symbols import get_symbol_meta
from cta.strategy.brooks.core.features.adapter import FeatureAdapter
from cta.strategy.brooks.core.model.score_gate import ScoreGate
from cta.strategy.brooks.core.risk import PortfolioRiskManager
from cta.strategy.brooks.core.strategy import BrooksV3Core
from cta.strategy.brooks.core.trade_log import TradeLogger

logger = logging.getLogger(__name__)


class BrooksV3LiveStrategy(CtaTemplate):
    """v3 在线策略外壳。

    vnpy 从 setting 注入:
    - config_path: str(可空,走默认 yaml)
    - model_path: str("none" / 绝对路径)
    - trade_log_dir: str
    """

    author = "Brooks v3"

    config_path: str = ""
    model_path: str = "none"
    trade_log_dir: str = "cta/strategy/brooks/report/live"
    initial_capital: float = 1_000_000.0

    parameters = [
        "config_path",
        "model_path",
        "trade_log_dir",
        "initial_capital",
    ]
    variables = ["pos"]

    def __init__(self, cta_engine, strategy_name: str,
                 vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.params = load_params(self.config_path or None)
        meta = get_symbol_meta(vt_symbol, self.params.contract)

        self.adapter = FeatureAdapter(mode="online")
        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager(size=200)

        if self.model_path and self.model_path.lower() != "none":
            gate = ScoreGate.load(self.model_path,
                                  threshold=self.params.model.threshold)
        else:
            gate = ScoreGate.passthrough()

        portfolio = PortfolioRiskManager(
            dd_threshold_half=self.params.risk.portfolio.dd_threshold_half,
            dd_threshold_quarter=self.params.risk.portfolio.dd_threshold_quarter,
            recover_to_full_at_new_high=self.params.risk.portfolio.recover_to_full_at_new_high,
        )
        log_dir = Path(self.trade_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        self.trade_logger = TradeLogger(
            output_path=log_dir / f"{vt_symbol.replace('.', '_')}_live.parquet"
        )
        self.core = BrooksV3Core(
            vt_symbol=vt_symbol,
            params=self.params,
            adapter=self.adapter,
            score_gate=gate,
            portfolio=portfolio,
            trade_logger=self.trade_logger,
            contract_size=meta.size,
            price_tick=meta.pricetick,
            initial_capital=self.initial_capital,
        )
        self._bar_idx = 0

    # ---- vnpy lifecycle ----
    def on_init(self) -> None:
        self.write_log("BrooksV3 策略初始化")
        self.load_bar(30)  # 读 30 天历史 1m → 预热 bg+am

    def on_start(self) -> None:
        self.write_log("BrooksV3 策略启动")

    def on_stop(self) -> None:
        self.write_log("BrooksV3 策略停止")
        self.core.finalize()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        row = self._bar_to_row(bar)
        # 增量更新在线特征
        feat_row = self.adapter.update_online(
            self.vt_symbol, self.params.intervals.ltf, row,
        )
        if feat_row is None:
            return
        decision = self.core.on_bar(self._bar_idx, feat_row)
        self._bar_idx += 1

        if decision.action == "BUY_OPEN" and self.pos == 0:
            self.buy(decision.price, decision.qty)
        elif decision.action == "SELL_CLOSE" and self.pos > 0:
            self.sell(decision.price, abs(self.pos))

    # ---- helpers ----
    def _bar_to_row(self, bar: BarData) -> pd.Series:
        return pd.Series({
            "datetime": pd.Timestamp(bar.datetime),
            "open": float(bar.open_price),
            "high": float(bar.high_price),
            "low": float(bar.low_price),
            "close": float(bar.close_price),
            "volume": float(bar.volume),
            "open_interest": float(getattr(bar, "open_interest", 0.0)),
            "turnover": float(getattr(bar, "turnover", 0.0)),
        })


__all__ = ["BrooksV3LiveStrategy"]
