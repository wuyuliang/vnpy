from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from vnpy.trader.object import BarData
from vnpy.trader.utility import ArrayManager, BarGenerator
from vnpy_ctastrategy import CtaTemplate

from .signal_store import SignalStore
from .sqlite_feature_service import FeatureGenerationService


@dataclass
class DailyFeatureRow:
    trade_date: str
    close: float
    atr: float
    candidate_flag: int
    opportunity_score: float
    opportunity_level: str
    stop_distance_atr: float
    expected_rr_rule: float


class TightRangeBreakoutStrategy(CtaTemplate):
    author = "OpenAI"

    feature_db_path: str = "./data/tight_range_features.sqlite"
    entry_score: float = 68.0
    min_expected_rr: float = 1.2
    risk_pct: float = 0.01
    fixed_size: int = 1
    use_fixed_size: int = 1
    trailing_atr_multiplier: float = 2.0
    stop_atr_multiplier: float = 1.0

    parameters = [
        "feature_db_path",
        "entry_score",
        "min_expected_rr",
        "risk_pct",
        "fixed_size",
        "use_fixed_size",
        "trailing_atr_multiplier",
        "stop_atr_multiplier",
    ]

    variables = [
        "last_score",
        "last_level",
        "last_candidate_flag",
        "entry_price_internal",
        "intra_trade_high",
        "stop_price_internal",
    ]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager(size=200)

        self.last_score: float = 0.0
        self.last_level: str = "D"
        self.last_candidate_flag: int = 0
        self.entry_price_internal: float = 0.0
        self.intra_trade_high: float = 0.0
        self.stop_price_internal: float = 0.0

        symbol, exchange = self.vt_symbol.split(".")
        self.symbol = symbol.upper()
        self.exchange = exchange.upper()

        self.feature_service = FeatureGenerationService(self.feature_db_path)
        self.signal_store = SignalStore(self.feature_db_path)
        self.feature_map: Dict[str, DailyFeatureRow] = {}

    def on_init(self) -> None:
        self.write_log("策略初始化")
        self.load_feature_map()
        self.load_bar(50)

    def on_start(self) -> None:
        self.write_log("策略启动")

    def on_stop(self) -> None:
        self.write_log("策略停止")

    def on_tick(self, tick) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        self.cancel_all()
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        trade_date = bar.datetime.strftime("%Y-%m-%d")
        feature = self.feature_map.get(trade_date)
        if feature is None:
            self.last_score = 0.0
            self.last_level = "D"
            self.last_candidate_flag = 0
            self.put_event()
            return

        self.last_score = feature.opportunity_score
        self.last_level = feature.opportunity_level
        self.last_candidate_flag = feature.candidate_flag

        if self.pos == 0:
            if (
                feature.candidate_flag == 1
                and feature.opportunity_score >= self.entry_score
                and feature.expected_rr_rule >= self.min_expected_rr
            ):
                volume = self.fixed_size if int(self.use_fixed_size) == 1 else max(1, self.calculate_size(bar.close, feature.atr))
                self.buy(bar.close * 1.01, volume)
                self.signal_store.save_signal(
                    symbol=self.symbol,
                    exchange=self.exchange,
                    trade_date=trade_date,
                    strategy_name=self.strategy_name,
                    action="BUY_SETUP",
                    score=feature.opportunity_score,
                    level=feature.opportunity_level,
                    candidate_flag=feature.candidate_flag,
                    close_price=bar.close_price,
                    atr=feature.atr,
                    comment=f"rr={feature.expected_rr_rule:.2f}",
                )
        else:
            self.intra_trade_high = max(self.intra_trade_high, bar.high_price)
            trailing_stop = self.intra_trade_high - self.trailing_atr_multiplier * feature.atr
            hard_stop = self.entry_price_internal - self.stop_atr_multiplier * feature.atr
            self.stop_price_internal = max(trailing_stop, hard_stop)
            self.sell(self.stop_price_internal, abs(self.pos), stop=True)

            if feature.opportunity_score < 45:
                self.sell(bar.close_price * 0.99, abs(self.pos))
                self.signal_store.save_signal(
                    symbol=self.symbol,
                    exchange=self.exchange,
                    trade_date=trade_date,
                    strategy_name=self.strategy_name,
                    action="WEAK_EXIT",
                    score=feature.opportunity_score,
                    level=feature.opportunity_level,
                    candidate_flag=feature.candidate_flag,
                    close_price=bar.close_price,
                    atr=feature.atr,
                    comment="score dropped below 45",
                )

        self.put_event()

    def on_order(self, order) -> None:
        pass

    def on_trade(self, trade) -> None:
        trade_date = trade.datetime.strftime("%Y-%m-%d")
        if trade.direction.value.upper() in {"LONG", "多"}:
            self.entry_price_internal = trade.price
            self.intra_trade_high = trade.price
            self.stop_price_internal = trade.price
            action = "BUY_FILLED"
        else:
            action = "SELL_FILLED"
            if self.pos == 0:
                self.entry_price_internal = 0.0
                self.intra_trade_high = 0.0
                self.stop_price_internal = 0.0

        feature = self.feature_map.get(trade_date)
        atr = feature.atr if feature else 0.0
        score = feature.opportunity_score if feature else 0.0
        level = feature.opportunity_level if feature else "D"
        candidate_flag = feature.candidate_flag if feature else 0

        self.signal_store.save_signal(
            symbol=self.symbol,
            exchange=self.exchange,
            trade_date=trade_date,
            strategy_name=self.strategy_name,
            action=action,
            score=score,
            level=level,
            candidate_flag=candidate_flag,
            close_price=trade.price,
            atr=atr,
            comment=f"tradeid={trade.vt_tradeid}",
        )
        self.put_event()

    def load_feature_map(self) -> None:
        df = self.feature_service.load_symbol_features(self.symbol, self.exchange)
        fmap: Dict[str, DailyFeatureRow] = {}
        if not df.empty:
            for _, row in df.iterrows():
                fmap[str(row["trade_date"])] = DailyFeatureRow(
                    trade_date=str(row["trade_date"]),
                    close=float(row["close"]),
                    atr=float(row["atr"] or 0.0),
                    candidate_flag=int(row["candidate_flag"] or 0),
                    opportunity_score=float(row["opportunity_score"] or 0.0),
                    opportunity_level=str(row["opportunity_level"] or "D"),
                    stop_distance_atr=float(row["stop_distance_atr"] or 0.0),
                    expected_rr_rule=float(row["expected_rr_rule"] or 0.0),
                )
        self.feature_map = fmap
        self.write_log(f"载入特征完成: {self.symbol}.{self.exchange} rows={len(self.feature_map)}")

    def calculate_size(self, price: float, atr: float) -> int:
        if atr <= 0 or price <= 0:
            return 1
        capital = float(getattr(self.cta_engine, "capital", 1_000_000))
        risk_amount = capital * float(self.risk_pct)
        unit_risk = atr * max(self.stop_atr_multiplier, 0.5)
        volume = int(max(risk_amount / max(unit_risk, 1e-6), 1))
        return volume
