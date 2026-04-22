"""低周期(5m / 1m)入场确认。

使用 pa_breakout_up_{lookback}、pa_breakout_strength_{lookback}、
pa_signal_strength_{lookback}、pa_expected_rr、pa_breakout_fail_{lookback}
判断:当前 bar 是否可作为入场触发。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from cta.strategy.brooks.config.params import LtfSignalCfg


@dataclass
class LtfEntry:
    direction: int                 # 1 bull, -1 bear, 0 none
    trigger_price: float           # 建议入场价(= close)
    stop_price: float              # 建议初始止损(= low - tick)
    breakout_strength: float
    signal_strength: float
    expected_rr: float
    reason: str = ""

    @property
    def should_enter(self) -> bool:
        return self.direction != 0


def detect_ltf_entry(
    feat: pd.Series | None,
    mtf_direction: int,
    cfg: LtfSignalCfg,
    price_tick: float = 1.0,
) -> LtfEntry:
    """低周期入场确认。

    只做 bull 侧:要求 pa_breakout_up_{lookback} == 1,并通过强度与 RR 过滤。
    """
    if feat is None:
        return LtfEntry(0, 0.0, 0.0, 0.0, 0.0, 0.0, "missing_feature")

    lb = cfg.lookback
    bu = int(feat.get(f"pa_breakout_up_{lb}", 0) or 0)
    bs = float(feat.get(f"pa_breakout_strength_{lb}", 0.0) or 0.0)
    ss = float(feat.get(f"pa_signal_strength_{lb}", 0.0) or 0.0)
    rr = float(feat.get("pa_expected_rr", 0.0) or 0.0)
    bf = int(feat.get(f"pa_breakout_fail_{lb}", 0) or 0)
    close = float(feat.get("close", 0.0) or 0.0)
    low = float(feat.get("low", close) or close)

    if any(math.isnan(v) for v in (bs, ss, rr, close, low)):
        return LtfEntry(0, close, low, bs, ss, rr, "nan_feature")

    if mtf_direction != 1:
        return LtfEntry(0, close, low, bs, ss, rr, "mtf_not_bull")

    if cfg.require_breakout_fire and bu != 1:
        return LtfEntry(0, close, low, bs, ss, rr, "no_breakout_fire")

    if abs(bs) < cfg.breakout_strength_min_abs or bs <= 0:
        return LtfEntry(0, close, low, bs, ss, rr, "weak_breakout_strength")

    if abs(ss) < cfg.signal_strength_min_abs or ss <= 0:
        return LtfEntry(0, close, low, bs, ss, rr, "weak_signal_strength")

    if rr < cfg.expected_rr_min:
        return LtfEntry(0, close, low, bs, ss, rr, "rr_too_low")

    if cfg.reject_if_breakout_fail and bf > 0:
        return LtfEntry(0, close, low, bs, ss, rr, "breakout_fail_flag")

    stop_price = low - price_tick
    return LtfEntry(
        direction=1,
        trigger_price=close,
        stop_price=stop_price,
        breakout_strength=bs,
        signal_strength=ss,
        expected_rr=rr,
        reason="ok",
    )


__all__ = ["LtfEntry", "detect_ltf_entry"]
