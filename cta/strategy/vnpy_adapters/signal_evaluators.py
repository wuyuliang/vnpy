"""Per-signal-type bar-stream evaluators for vnpy CtaTemplate wrapper.

为什么独立模块
--------------
[baseline_strategy.py](baseline_strategy.py) 主要管 vnpy lifecycle + 风控；
每个 signal_type 的入场逻辑各异，集中放一处会让单文件 > 500 行。本模块按
signal_type 提供 **简化版** evaluator：

- 仅基于流式 OHLCV bar buffer（不依赖离线 feature parquet）
- 复杂特征（tr_valid / atr_upper / 等）由 sim_runner 注入的 `extra_filter`
  在外层做完整模型决策
- 用 dispatch table 让 wrapper 按 ``signal_type`` 路由

7 个 signal_type 对应 [config/baseline_skill_suite_config.py:12](../../config/baseline_skill_suite_config.py:12)
的 ``BASELINE_SIGNAL_TYPES``：
``donchian_breakout`` / ``atr_breakout`` / ``tight_range_breakout`` /
``breakout_pullback_continuation`` / ``trend_acceleration_breakout`` /
``bull_pullback_continuation`` / ``bull_volatility_contraction_breakout``。
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

# Decision dict 协议
#   {"side": "long_open" | "short_open", "price": float, "trigger": str}
SignalDecision = dict[str, Any]
Evaluator = Callable[[Sequence[Any], "EvaluatorConfig"], "SignalDecision | None"]


class EvaluatorConfig:
    """轻量配置（避免与 BaselineStrategyConfig 双向依赖）。"""

    __slots__ = (
        "donchian_window",
        "atr_window",
        "atr_band_k",
        "tight_range_window",
        "tight_range_atr_ratio_max",
        "pullback_lookback",
        "pullback_pct",
        "trend_ma_window",
        "trend_slope_min_pct",
        "bull_ma_window",
        "bull_pullback_pct",
        "vol_contraction_window",
        "vol_contraction_ratio",
    )

    def __init__(
        self,
        *,
        donchian_window: int = 20,
        atr_window: int = 14,
        atr_band_k: float = 2.0,
        tight_range_window: int = 10,
        tight_range_atr_ratio_max: float = 0.6,
        pullback_lookback: int = 5,
        pullback_pct: float = 0.005,
        trend_ma_window: int = 20,
        trend_slope_min_pct: float = 0.003,
        bull_ma_window: int = 20,
        bull_pullback_pct: float = 0.005,
        vol_contraction_window: int = 10,
        vol_contraction_ratio: float = 0.7,
    ) -> None:
        self.donchian_window = int(donchian_window)
        self.atr_window = int(atr_window)
        self.atr_band_k = float(atr_band_k)
        self.tight_range_window = int(tight_range_window)
        self.tight_range_atr_ratio_max = float(tight_range_atr_ratio_max)
        self.pullback_lookback = int(pullback_lookback)
        self.pullback_pct = float(pullback_pct)
        self.trend_ma_window = int(trend_ma_window)
        self.trend_slope_min_pct = float(trend_slope_min_pct)
        self.bull_ma_window = int(bull_ma_window)
        self.bull_pullback_pct = float(bull_pullback_pct)
        self.vol_contraction_window = int(vol_contraction_window)
        self.vol_contraction_ratio = float(vol_contraction_ratio)


def _ohlc(bar: Any) -> tuple[float, float, float, float]:
    return (
        float(getattr(bar, "open_price", 0.0)),
        float(getattr(bar, "high_price", 0.0)),
        float(getattr(bar, "low_price", 0.0)),
        float(getattr(bar, "close_price", 0.0)),
    )


def _sma(values: Sequence[float], window: int) -> float:
    if len(values) < window or window <= 0:
        return float("nan")
    return float(sum(values[-window:]) / window)


def _atr(bars: Sequence[Any], window: int) -> float:
    """Simple TR mean over last `window` bars; needs at least window+1 bars."""
    if len(bars) < window + 1 or window <= 0:
        return float("nan")
    trs: list[float] = []
    for i in range(len(bars) - window, len(bars)):
        _o, h, lo, c = _ohlc(bars[i])
        prev_close = float(getattr(bars[i - 1], "close_price", 0.0))
        tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        trs.append(tr)
    return float(sum(trs) / len(trs))


# ---------------------------------------------------------------------------
# 7 evaluators
# ---------------------------------------------------------------------------


def evaluate_donchian_breakout(
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """Close 突破前 N 根 high/low。"""
    n = cfg.donchian_window
    if len(bars) < n + 1:
        return None
    recent = bars[-(n + 1):-1]
    current = bars[-1]
    upper = max(float(getattr(b, "high_price", 0.0)) for b in recent)
    lower = min(float(getattr(b, "low_price", 0.0)) for b in recent)
    _o, _h, _lo, close = _ohlc(current)
    if close > upper:
        return {"side": "long_open", "price": close, "trigger": "donchian_upper_break"}
    if close < lower:
        return {"side": "short_open", "price": close, "trigger": "donchian_lower_break"}
    return None


def evaluate_atr_breakout(
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """Close 突破 ``SMA ± k*ATR`` 带（前一根未突破）。"""
    n = cfg.atr_window
    if len(bars) < n + 2:
        return None
    closes = [float(getattr(b, "close_price", 0.0)) for b in bars[-(n + 2):]]
    sma_now = _sma(closes[1:], n)
    sma_prev = _sma(closes[:-1], n)
    atr_now = _atr(bars[-(n + 1):], n)
    atr_prev = _atr(bars[-(n + 2):-1], n)
    if not (atr_now > 0 and atr_prev > 0):
        return None
    upper_now = sma_now + cfg.atr_band_k * atr_now
    lower_now = sma_now - cfg.atr_band_k * atr_now
    upper_prev = sma_prev + cfg.atr_band_k * atr_prev
    lower_prev = sma_prev - cfg.atr_band_k * atr_prev
    close_now = closes[-1]
    close_prev = closes[-2]
    if close_now > upper_now and close_prev <= upper_prev:
        return {"side": "long_open", "price": close_now, "trigger": "atr_upper_break"}
    if close_now < lower_now and close_prev >= lower_prev:
        return {"side": "short_open", "price": close_now, "trigger": "atr_lower_break"}
    return None


def evaluate_tight_range_breakout(
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """前 N 根 range / ATR 比 < 阈值 → 收紧；本根突破上下界。"""
    n = cfg.tight_range_window
    if len(bars) < n + cfg.atr_window + 1:
        return None
    window = bars[-(n + 1):-1]
    upper = max(float(getattr(b, "high_price", 0.0)) for b in window)
    lower = min(float(getattr(b, "low_price", 0.0)) for b in window)
    range_size = upper - lower
    atr_v = _atr(bars[-(cfg.atr_window + 1):], cfg.atr_window)
    if not (atr_v > 0):
        return None
    ratio = range_size / (atr_v * n)
    if ratio > cfg.tight_range_atr_ratio_max:
        return None  # 还没收紧
    _o, _h, _lo, close = _ohlc(bars[-1])
    if close > upper:
        return {"side": "long_open", "price": close, "trigger": "tight_range_upper_break"}
    if close < lower:
        return {"side": "short_open", "price": close, "trigger": "tight_range_lower_break"}
    return None


def evaluate_breakout_pullback_continuation(
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """前期 donchian 突破后回踩，再次反弹时入场。"""
    n = cfg.donchian_window
    lb = cfg.pullback_lookback
    if len(bars) < n + lb + 1:
        return None
    pre_break_window = bars[-(n + lb + 1):-(lb + 1)]
    upper = max(float(getattr(b, "high_price", 0.0)) for b in pre_break_window)
    lower = min(float(getattr(b, "low_price", 0.0)) for b in pre_break_window)
    pullback_window = bars[-(lb + 1):-1]
    pb_high = max(float(getattr(b, "high_price", 0.0)) for b in pullback_window)
    pb_low = min(float(getattr(b, "low_price", 0.0)) for b in pullback_window)
    _o, _h, _lo, close = _ohlc(bars[-1])
    # long：上行突破后回踩到 upper 附近但仍 ≥ upper * (1 - pct)
    if pb_high > upper and close > pb_high * (1.0 - cfg.pullback_pct):
        return {"side": "long_open", "price": close, "trigger": "breakout_pullback_long"}
    if pb_low < lower and close < pb_low * (1.0 + cfg.pullback_pct):
        return {"side": "short_open", "price": close, "trigger": "breakout_pullback_short"}
    return None


def evaluate_trend_acceleration_breakout(
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """MA 斜率显著（>min_pct）+ close > MA → 趋势加速。"""
    n = cfg.trend_ma_window
    if len(bars) < n + 1:
        return None
    closes = [float(getattr(b, "close_price", 0.0)) for b in bars[-(n + 1):]]
    sma_now = _sma(closes[1:], n)
    sma_prev = _sma(closes[:-1], n)
    if not (sma_now > 0 and sma_prev > 0):
        return None
    slope_pct = (sma_now - sma_prev) / sma_prev
    close_now = closes[-1]
    if slope_pct >= cfg.trend_slope_min_pct and close_now > sma_now:
        return {"side": "long_open", "price": close_now, "trigger": "trend_accel_long"}
    if slope_pct <= -cfg.trend_slope_min_pct and close_now < sma_now:
        return {"side": "short_open", "price": close_now, "trigger": "trend_accel_short"}
    return None


def evaluate_bull_pullback_continuation(
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """MA 上方 + 当前 close 回到 MA 附近（pullback_pct）→ 牛市回踩多。"""
    n = cfg.bull_ma_window
    if len(bars) < n + 1:
        return None
    closes = [float(getattr(b, "close_price", 0.0)) for b in bars[-(n + 1):]]
    sma_now = _sma(closes[1:], n)
    sma_prev = _sma(closes[:-1], n)
    if not (sma_now > 0 and sma_prev > 0):
        return None
    # 趋势确认：sma_now > sma_prev（牛）
    close_now = closes[-1]
    if sma_now > sma_prev and abs(close_now - sma_now) / sma_now < cfg.bull_pullback_pct:
        return {"side": "long_open", "price": close_now, "trigger": "bull_pullback_long"}
    if sma_now < sma_prev and abs(close_now - sma_now) / sma_now < cfg.bull_pullback_pct:
        return {"side": "short_open", "price": close_now, "trigger": "bull_pullback_short"}
    return None


def evaluate_bull_volatility_contraction_breakout(
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """近 N 根 ATR < 之前 N 根 ATR * ratio → 波动收缩后突破。

    codex P2-E 修复：ATR 计算改用 ``cfg.atr_window``（之前误用 ``n=vol_contraction_window``）。
    """
    n = cfg.vol_contraction_window
    a = cfg.atr_window
    if len(bars) < 2 * n + a + 1:
        return None
    atr_recent = _atr(bars[-(a + 1):], a)
    atr_prior = _atr(bars[-(n + a + 1):-(n)], a)
    if not (atr_recent > 0 and atr_prior > 0):
        return None
    if atr_recent / atr_prior > cfg.vol_contraction_ratio:
        return None  # 还没收缩
    # 收缩后看是否破前 N 根 high / low
    window = bars[-(n + 1):-1]
    upper = max(float(getattr(b, "high_price", 0.0)) for b in window)
    lower = min(float(getattr(b, "low_price", 0.0)) for b in window)
    _o, _h, _lo, close = _ohlc(bars[-1])
    if close > upper:
        return {"side": "long_open", "price": close, "trigger": "bull_vol_contract_break_long"}
    if close < lower:
        return {"side": "short_open", "price": close, "trigger": "bull_vol_contract_break_short"}
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

SIGNAL_EVALUATORS: dict[str, Evaluator] = {
    "donchian_breakout": evaluate_donchian_breakout,
    "atr_breakout": evaluate_atr_breakout,
    "tight_range_breakout": evaluate_tight_range_breakout,
    "breakout_pullback_continuation": evaluate_breakout_pullback_continuation,
    "trend_acceleration_breakout": evaluate_trend_acceleration_breakout,
    "bull_pullback_continuation": evaluate_bull_pullback_continuation,
    "bull_volatility_contraction_breakout": evaluate_bull_volatility_contraction_breakout,
}


def dispatch_signal(
    signal_type: str,
    bars: Sequence[Any],
    cfg: EvaluatorConfig,
) -> "SignalDecision | None":
    """按 signal_type 路由到对应 evaluator；未知 signal_type 返回 None。"""
    evaluator = SIGNAL_EVALUATORS.get(str(signal_type))
    if evaluator is None:
        return None
    return evaluator(bars, cfg)


__all__ = [
    "EvaluatorConfig",
    "SIGNAL_EVALUATORS",
    "SignalDecision",
    "dispatch_signal",
    "evaluate_donchian_breakout",
    "evaluate_atr_breakout",
    "evaluate_tight_range_breakout",
    "evaluate_breakout_pullback_continuation",
    "evaluate_trend_acceleration_breakout",
    "evaluate_bull_pullback_continuation",
    "evaluate_bull_volatility_contraction_breakout",
]
