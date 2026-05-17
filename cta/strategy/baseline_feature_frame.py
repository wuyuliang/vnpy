"""Feature-frame preparation for baseline strategies."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.config.skill_tight_range_breakout_config import StrategyConfig
from cta.skills.price_action.breakout_pullback import detect_breakout_pullback
from cta.skills.price_action.tight_range_breakout import resolve_breakout_trigger
from cta.skills.trend_strategies.atr_breakout import compute_atr_channel
from cta.skills.trend_strategies.donchian_breakout import compute_donchian
from cta.strategy.baseline_helpers import _compute_atr14
from cta.strategy.skill_tight_range_breakout import prepare_strategy_frame


def prepare_master_feature_frame(
    bars: pd.DataFrame,
    interval: str = "day",
    *,
    symbol: str | None = None,
    pricetick: float | None = None,
) -> pd.DataFrame:
    """Prepare one dataframe containing baseline features for all strategies."""
    out = bars.copy().reset_index(drop=True)
    if "datetime" in out.columns:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
        out = (
            out.dropna(subset=["datetime"])
            .sort_values("datetime")
            .drop_duplicates("datetime", keep="last")
            .reset_index(drop=True)
        )

    for c in ("open", "high", "low", "close", "volume"):
        if c not in out.columns:
            raise KeyError(f"prepare_master_feature_frame missing column: {c}")
        out[c] = out[c].astype(float)
    if "open_interest" not in out.columns:
        out["open_interest"] = 0.0
    if "turnover" not in out.columns:
        out["turnover"] = 0.0

    prev_close = out["close"].shift(1)
    close_s = out["close"].astype(float)
    if pricetick is None or not np.isfinite(float(pricetick)) or float(pricetick) <= 0:
        tick_ref = (close_s * 0.0005).abs()
    else:
        tick_ref = pd.Series(
            np.full(len(out), float(pricetick), dtype=float),
            index=out.index,
        )
    one_way = (out["high"] - out["low"]).abs() <= tick_ref

    from cta.config.symbol_cluster_config import infer_symbol_limit_pct

    limit_pct = float(infer_symbol_limit_pct(str(symbol) if symbol else "", default_pct=0.05))
    buffer_abs = 0.001
    pct_change = (close_s / prev_close - 1.0).where(prev_close.notna())
    limit_up = pct_change >= (limit_pct - buffer_abs)
    limit_down = pct_change <= -(limit_pct - buffer_abs)
    out["is_one_way_bar"] = one_way.astype(int)
    out["is_limit_up_close"] = limit_up.fillna(False).astype(int)
    out["is_limit_down_close"] = limit_down.fillna(False).astype(int)

    out["atr14"] = _compute_atr14(out)

    don_df = compute_donchian(out, n_entry=55, n_exit=20, interval=interval)
    for c in ("don_upper_entry", "don_lower_entry", "don_upper_exit", "don_lower_exit", "don_atr20"):
        out[c] = don_df[c].astype(float)

    atr_df = compute_atr_channel(out, ma_n=20, atr_n=14, k=2.5, interval=interval)
    for c in ("atr_ma", "atr_value", "atr_upper", "atr_lower"):
        out[c] = atr_df[c].astype(float)

    tr_cfg = StrategyConfig(
        lookback=10,
        alpha=1.5,
        min_count=5,
        min_breakout_score=0.30,
        lots=1,
        risk_per_trade_pct=0.005,
        initial_stop_atr_mult=1.5,
        trailing_stop_atr_mult=2.2,
        max_holding_bars=20,
        align_trend_direction=False,
        trade_side_mode="both",
    )
    tr_df = prepare_strategy_frame(out, tr_cfg, interval=interval)
    if len(tr_df) != len(out):
        raise ValueError(f"prepare_strategy_frame rows mismatch: out={len(out)} tr_df={len(tr_df)}")
    for c in (
        "tr_valid",
        "tr_upper",
        "tr_lower",
        "tr_range_atr",
        "tr_count",
        "tr_direction_bias",
        "trend_score",
        "trend_dir",
        "trend_strength",
        "breakout_score",
        "breakout_pass",
    ):
        if c in tr_df.columns:
            out[c] = tr_df[c]

    anchor = pd.DataFrame(index=out.index)
    anchor["breakout_level"] = np.nan
    anchor["breakout_direction"] = ""
    long_mask = out["close"].astype(float) > out["don_upper_entry"].astype(float)
    short_mask = out["close"].astype(float) < out["don_lower_entry"].astype(float)
    anchor.loc[long_mask, "breakout_level"] = out.loc[long_mask, "don_upper_entry"]
    anchor.loc[long_mask, "breakout_direction"] = "long"
    anchor.loc[short_mask, "breakout_level"] = out.loc[short_mask, "don_lower_entry"]
    anchor.loc[short_mask, "breakout_direction"] = "short"

    pb_df = detect_breakout_pullback(
        out,
        breakout_df=anchor,
        max_bars_since_brk=20,
        max_pullback_atr=1.5,
        interval=interval,
    )
    for c in (
        "bp_valid",
        "bp_direction",
        "bp_breakout_level",
        "bp_pullback_low",
        "bp_bars_since_breakout",
        "bp_confirmed",
    ):
        out[c] = pb_df[c]

    return out


__all__ = ["prepare_master_feature_frame"]

