"""Skill-based tight range breakout strategy.

策略假设
--------
1. 窄幅整理后更容易出现方向性突破。
2. 突破质量可由价格行为与量价结构打分过滤。
3. 顺趋势突破胜率更高，逆势信号应被抑制。

适用品种
--------
中国商品期货主力连续合约（例如 RB0/HC0/I0/MA0/TA0 等）。

适用周期
--------
默认日线；接口兼容 minute/minute5/minute15/minute30/minute60。

信号定义
--------
1. tight-range 检测：来自 ``cta.skills.price_action.tight_range_breakout``。
2. breakout quality：来自 ``cta.skills.filtering_scoring.breakout_quality``。
3. trend filter：来自 ``cta.skills.market_regime.trend``。

开仓规则
--------
当 tight-range 生效且 breakout quality 通过阈值，发出 stop 入场单。

平仓规则
--------
1. 触发动态止损（初始止损 + ATR 跟踪止损）后平仓。
2. 超过最大持仓 bars 后平仓。

止损规则
--------
基于 ATR 的初始止损与跟踪止损。

仓位管理规则
------------
固定最小手数 + 风险预算反推手数（单笔风险比例）。

手续费与滑点假设
----------------
由合约元数据（multiplier/tick_size/commission_rate/slippage_ticks）提供。

可能失效的市场环境
------------------
1. 长时间低波动无趋势、假突破频发。
2. 跳空极端行情导致 next-open 成交偏离止损意图。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from cta.config.skill_tight_range_breakout_config import StrategyConfig
from cta.skills.filtering_scoring.breakout_quality import (
    breakout_quality_gate,
    score_breakout,
)
from cta.skills.market_regime.trend import compute_trend_state
from cta.skills.price_action.tight_range_breakout import (
    TightRangeSetup,
    detect_tight_range,
    resolve_breakout_trigger,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    exchange: str
    multiplier: float
    tick_size: float
    commission_rate: float
    slippage_ticks: float = 1.5

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"


def _validate_ohlcv(df: pd.DataFrame) -> None:
    need = {"open", "high", "low", "close", "volume"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"strategy input missing columns: {miss}")


def _compute_atr14(df: pd.DataFrame) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=7).mean()


def prepare_strategy_frame(
    bars: pd.DataFrame,
    cfg: StrategyConfig,
    interval: str = "day",
) -> pd.DataFrame:
    """Build strategy-ready dataframe from OHLCV bars."""
    _validate_ohlcv(bars)
    out = bars.copy()
    if "datetime" in out.columns:
        out = out.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    else:
        out = out.reset_index(drop=True)

    for c in ("open", "high", "low", "close", "volume"):
        out[c] = out[c].astype(float)

    out["atr14"] = _compute_atr14(out)
    out = detect_tight_range(
        out,
        lookback=cfg.lookback,
        alpha=cfg.alpha,
        min_count=cfg.min_count,
        interval=interval,
    )
    trend_df = compute_trend_state(out)
    out["trend_score"] = trend_df["trend_score"].astype(float)
    out["trend_dir"] = trend_df["trend_dir"].astype(int)
    out["trend_strength"] = trend_df["trend_strength"].astype(float)
    out["trend_maturity"] = trend_df["trend_maturity"].astype(str)

    scores: list[float] = []
    passed: list[bool] = []
    for i in range(len(out)):
        atr_v = float(out.at[i, "atr14"]) if pd.notna(out.at[i, "atr14"]) else np.nan
        if (not bool(out.at[i, "tr_valid"])) or (not np.isfinite(atr_v)) or atr_v <= 0:
            scores.append(np.nan)
            passed.append(False)
            continue

        upper = float(out.at[i, "tr_upper"])
        lower = float(out.at[i, "tr_lower"])
        close = float(out.at[i, "close"])
        if close >= upper:
            breakout_level = upper
        elif close <= lower:
            breakout_level = lower
        else:
            breakout_level = upper if int(out.at[i, "tr_direction_bias"]) >= 0 else lower

        quality = score_breakout(
            out,
            breakout_bar_idx=i,
            breakout_level=breakout_level,
            atr=atr_v,
            interval=interval,
            follow_bars=0,
        )
        scores.append(float(quality.score))
        passed.append(bool(breakout_quality_gate(quality, cfg.min_breakout_score)))

    out["breakout_score"] = scores
    out["breakout_pass"] = pd.Series(passed, index=out.index, dtype=bool)
    return out


class SkillTightRangeBreakoutStrategy:
    """Event-driven strategy with ``on_bar(i, bar, position)`` interface."""

    def __init__(
        self,
        frame: pd.DataFrame,
        cfg: StrategyConfig,
        contract: ContractSpec,
        capital_base: float = 1_000_000.0,
    ) -> None:
        self.frame = frame.reset_index(drop=True)
        self.cfg = cfg
        self.contract = contract
        self.capital_base = float(capital_base)

        self._prev_position: int = 0
        self._entry_index: int = -1
        self._entry_price: float = 0.0
        self._high_since_entry: float = float("-inf")
        self._low_since_entry: float = float("inf")
        self._stop_price: float | None = None
        self._pending_initial_stop: float | None = None

    def _is_side_allowed(self, side: str) -> bool:
        mode = str(self.cfg.trade_side_mode).strip().lower()
        side_norm = str(side).strip().lower()
        if mode == "both":
            return side_norm in {"long", "short"}
        if mode == "long":
            return side_norm == "long"
        if mode == "short":
            return side_norm == "short"
        return False

    def compute_lots(self, atr_value: float) -> int:
        """Compute lots from risk budget with a hard floor of 1 lot."""
        base_lots = max(1, int(self.cfg.lots))
        atr_v = float(atr_value)
        if (not np.isfinite(atr_v)) or atr_v <= 0:
            return base_lots

        risk_budget = self.capital_base * max(float(self.cfg.risk_per_trade_pct), 0.0)
        stop_distance = atr_v * max(float(self.cfg.initial_stop_atr_mult), 1e-6)
        risk_per_lot = stop_distance * max(float(self.contract.multiplier), 1e-9)
        if risk_per_lot <= 0:
            return base_lots
        risk_lots = int(risk_budget / risk_per_lot)
        return max(base_lots, max(1, risk_lots))

    def _sync_position_state(self, i: int, bar: pd.Series, position: int) -> None:
        if self._prev_position == 0 and position != 0:
            atr_v = float(bar.get("atr14", np.nan))
            if (not np.isfinite(atr_v)) or atr_v <= 0:
                atr_v = max(float(bar["high"]) - float(bar["low"]), self.contract.tick_size)

            self._entry_index = i
            self._entry_price = float(bar["open"])
            self._high_since_entry = float(bar["high"])
            self._low_since_entry = float(bar["low"])

            if position > 0:
                base_stop = self._entry_price - self.cfg.initial_stop_atr_mult * atr_v
                if self._pending_initial_stop is not None:
                    base_stop = max(base_stop, float(self._pending_initial_stop))
                self._stop_price = base_stop
            else:
                base_stop = self._entry_price + self.cfg.initial_stop_atr_mult * atr_v
                if self._pending_initial_stop is not None:
                    base_stop = min(base_stop, float(self._pending_initial_stop))
                self._stop_price = base_stop
            logger.debug(
                "opened %s at i=%s entry=%.4f stop=%.4f",
                "long" if position > 0 else "short",
                i,
                self._entry_price,
                float(self._stop_price),
            )
            self._pending_initial_stop = None

        if self._prev_position != 0 and position == 0:
            self._entry_index = -1
            self._entry_price = 0.0
            self._high_since_entry = float("-inf")
            self._low_since_entry = float("inf")
            self._stop_price = None
            self._pending_initial_stop = None

    def _build_entry_order(self, row: pd.Series) -> list[dict[str, Any]]:
        if not bool(row.get("tr_valid", False)):
            return []
        atr_v = float(row.get("atr14", np.nan))
        if (not np.isfinite(atr_v)) or atr_v <= 0:
            return []

        setup = TightRangeSetup(
            valid=bool(row["tr_valid"]),
            upper=float(row["tr_upper"]),
            lower=float(row["tr_lower"]),
            range_atr=float(row["tr_range_atr"]) if pd.notna(row["tr_range_atr"]) else np.nan,
            count=int(row["tr_count"]),
            direction_bias=int(row["tr_direction_bias"]),
        )
        trigger = resolve_breakout_trigger(setup, row, self.contract.tick_size)
        if trigger is None:
            return []
        if not bool(row.get("breakout_pass", False)):
            return []
        if not self._is_side_allowed(str(trigger["side"])):
            return []

        trend_dir = int(row.get("trend_dir", 0))
        if self.cfg.align_trend_direction and trend_dir != 0:
            if trigger["side"] == "long" and trend_dir < 0:
                return []
            if trigger["side"] == "short" and trend_dir > 0:
                return []

        lots = self.compute_lots(atr_v)
        self._pending_initial_stop = float(trigger["stop"])
        return [
            {
                "side": str(trigger["side"]),
                "lots": int(lots),
                "order_type": "stop",
                "price": float(trigger["trigger"]),
                "symbol": self.contract.vt_symbol,
                "multiplier": float(self.contract.multiplier),
                "commission_rate": float(self.contract.commission_rate),
                "tick_size": float(self.contract.tick_size),
            }
        ]

    def _build_exit_order(self, i: int, row: pd.Series, position: int) -> list[dict[str, Any]]:
        if self._entry_index < 0:
            return []

        atr_v = float(row.get("atr14", np.nan))
        if (not np.isfinite(atr_v)) or atr_v <= 0:
            atr_v = max(float(row["high"]) - float(row["low"]), self.contract.tick_size)

        bars_held = i - self._entry_index
        high = float(row["high"])
        low = float(row["low"])

        if position > 0:
            self._high_since_entry = max(self._high_since_entry, high)
            trail = self._high_since_entry - self.cfg.trailing_stop_atr_mult * atr_v
            if self._stop_price is None:
                self._stop_price = trail
            else:
                self._stop_price = max(float(self._stop_price), trail)
            stop_hit = low <= float(self._stop_price)
        else:
            self._low_since_entry = min(self._low_since_entry, low)
            trail = self._low_since_entry + self.cfg.trailing_stop_atr_mult * atr_v
            if self._stop_price is None:
                self._stop_price = trail
            else:
                self._stop_price = min(float(self._stop_price), trail)
            stop_hit = high >= float(self._stop_price)

        time_stop_hit = bars_held >= int(self.cfg.max_holding_bars)
        if not (stop_hit or time_stop_hit):
            return []

        return [
            {
                "side": "flat",
                "lots": int(abs(position)),
                "order_type": "market",
                "symbol": self.contract.vt_symbol,
                "multiplier": float(self.contract.multiplier),
                "commission_rate": float(self.contract.commission_rate),
                "tick_size": float(self.contract.tick_size),
            }
        ]

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        """Strategy entry point for event-driven engine."""
        self._sync_position_state(i, bar, position)
        if position == 0:
            orders = self._build_entry_order(bar)
        else:
            orders = self._build_exit_order(i, bar, position)
        self._prev_position = int(position)
        return orders


__all__ = [
    "ContractSpec",
    "prepare_strategy_frame",
    "SkillTightRangeBreakoutStrategy",
]
