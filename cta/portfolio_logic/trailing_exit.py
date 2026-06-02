"""ATR trailing-exit simulation helpers."""
from __future__ import annotations

from typing import Any
import logging

import numpy as np
import pandas as pd

from cta.portfolio_logic.config import (
    HorizonExtendConfig,
    IntervalTrailingParams,
    TrailingExitConfig,
    normalize_portfolio_interval,
)
from cta.portfolio_logic.pyramid_manager import PyramidPosition

logger = logging.getLogger(__name__)


def _resolve_interval_params(cfg: TrailingExitConfig, interval: str) -> IntervalTrailingParams:
    key = normalize_portfolio_interval(interval)
    if key in cfg.interval_params:
        return cfg.interval_params[key]
    if cfg.default_interval_params_key in cfg.interval_params:
        return cfg.interval_params[cfg.default_interval_params_key]
    return next(iter(cfg.interval_params.values()))


def _is_trailing_regime(cfg: TrailingExitConfig, regime_label: str | None) -> bool:
    if regime_label is None:
        return False
    return str(regime_label).strip().lower() in {x.lower() for x in cfg.activate_only_when_regime}


def _is_extend_regime(horizon_cfg: HorizonExtendConfig | None, regime_label: str | None) -> bool:
    if horizon_cfg is None or not bool(horizon_cfg.enabled):
        return False
    if regime_label is None:
        return False
    return str(regime_label).strip().lower() in {x.lower() for x in horizon_cfg.extend_when_regime}


def _empty_result(
    *,
    entry_price_hint: float,
    planned_exit_ts: pd.Timestamp,
    reason: str,
) -> dict[str, Any]:
    return {
        "entry_fill_datetime": pd.NaT,
        "entry_fill_price": float(entry_price_hint) if np.isfinite(entry_price_hint) else float("nan"),
        "planned_exit_datetime": planned_exit_ts,
        "planned_exit_price": float("nan"),
        "stop_loss_price": float("nan"),
        "stop_hit_datetime": pd.NaT,
        "stop_hit_price": float("nan"),
        "stop_triggered": 0,
        "final_exit_datetime": planned_exit_ts,
        "final_exit_price": float("nan"),
        "exit_reason": reason,
        "price_return_pct": float("nan"),
        "trailing_activated": 0,
        "trailing_stop_price": float("nan"),
        "extensions_used": 0,
        "trailing_tp_active": 0,
        "trailing_tp_highwater": float("nan"),
        "horizon_extended_to": 0,
    }


def _effective_stop_for_side(side: str, hard_stop: float, trail_stop: float) -> float:
    if str(side).strip().lower() == "short":
        return float(min(hard_stop, trail_stop))
    return float(max(hard_stop, trail_stop))


class TrailingExitSimulator:
    """Streaming per-bar trailing stop simulator for live/sim loops."""

    def __init__(self, cfg: TrailingExitConfig) -> None:
        self.cfg = cfg

    def update_one_bar(
        self,
        positions: list[PyramidPosition],
        bar: dict[str, Any] | pd.Series,
        *,
        regime_label_by_symbol: dict[tuple[str, str], str] | None = None,
    ) -> list[dict[str, Any]]:
        if regime_label_by_symbol is None:
            regime_label_by_symbol = {}
        row = dict(bar)
        bar_symbol = str(row.get("symbol", "")).upper()
        bar_exchange = str(row.get("exchange", "")).upper()
        bar_dt = pd.Timestamp(row.get("datetime"))
        bar_open = pd.to_numeric(pd.Series([row.get("open", np.nan)]), errors="coerce").iloc[0]
        bar_high = pd.to_numeric(pd.Series([row.get("high", np.nan)]), errors="coerce").iloc[0]
        bar_low = pd.to_numeric(pd.Series([row.get("low", np.nan)]), errors="coerce").iloc[0]
        if pd.isna(bar_dt):
            return []
        events: list[dict[str, Any]] = []

        for pos in positions:
            if str(pos.symbol).upper() != bar_symbol or str(pos.exchange).upper() != bar_exchange:
                continue
            if np.isfinite(bar_high):
                pos.running_high = max(float(pos.running_high), float(bar_high))
            if np.isfinite(bar_low):
                pos.running_low = min(float(pos.running_low), float(bar_low))
            regime_label = regime_label_by_symbol.get((bar_symbol, bar_exchange))

            for layer in pos.active_layers:
                interval_params = _resolve_interval_params(self.cfg, layer.interval)
                atr_pct = float(layer.atr_pct_at_entry)
                atr_abs = float(layer.entry_price * atr_pct) if np.isfinite(atr_pct) and atr_pct > 0 else float("nan")
                trailing_allowed = bool(
                    self.cfg.enabled
                    and _is_trailing_regime(self.cfg, regime_label)
                    and np.isfinite(atr_abs)
                    and atr_abs > 0.0
                )

                if trailing_allowed:
                    if str(pos.direction).lower() == "short":
                        profit_atr = (float(layer.entry_price) - float(pos.running_low)) / atr_abs
                        if profit_atr >= float(interval_params.activation_profit_atr):
                            layer.trailing_activated = True
                            new_stop = float(pos.running_low) + float(interval_params.atr_multiplier) * atr_abs
                            if self.cfg.update_only_in_favor:
                                layer.trail_stop_price = min(float(layer.trail_stop_price), float(new_stop))
                            else:
                                layer.trail_stop_price = float(new_stop)
                    else:
                        profit_atr = (float(pos.running_high) - float(layer.entry_price)) / atr_abs
                        if profit_atr >= float(interval_params.activation_profit_atr):
                            layer.trailing_activated = True
                            new_stop = float(pos.running_high) - float(interval_params.atr_multiplier) * atr_abs
                            if self.cfg.update_only_in_favor:
                                layer.trail_stop_price = max(float(layer.trail_stop_price), float(new_stop))
                            else:
                                layer.trail_stop_price = float(new_stop)

                stop = float(layer.effective_stop)
                side = str(pos.direction).lower()
                stop_hit = False
                stop_fill = float(stop)
                if side == "short":
                    if np.isfinite(bar_high) and float(bar_high) >= stop:
                        stop_hit = True
                        stop_fill = max(float(stop), float(bar_open)) if np.isfinite(bar_open) else float(stop)
                else:
                    if np.isfinite(bar_low) and float(bar_low) <= stop:
                        stop_hit = True
                        stop_fill = min(float(stop), float(bar_open)) if np.isfinite(bar_open) else float(stop)
                if not stop_hit:
                    continue
                layer.exited = True
                events.append(
                    {
                        "pos_id": pos.pos_id,
                        "symbol": pos.symbol,
                        "exchange": pos.exchange,
                        "direction": pos.direction,
                        "layer_id": int(layer.layer_id),
                        "exit_datetime": pd.Timestamp(bar_dt),
                        "exit_price": float(stop_fill),
                        "reason": "trailing_stop" if layer.trailing_activated else "hard_stop",
                        "stop_price": float(stop),
                    }
                )
        return events


def simulate_trailing_exit(
    *,
    side: str,
    entry_ts: pd.Timestamp,
    planned_exit_ts: pd.Timestamp,
    entry_price_hint: float,
    stop_loss_pct: float,
    bars: pd.DataFrame,
    interval: str,
    atr_pct_at_entry: float | None,
    regime_label: str | None,
    cfg: TrailingExitConfig,
    horizon_cfg: HorizonExtendConfig | None = None,
    hold_extend_score: float | None = None,
    recommended_extension_bars: int | None = None,
    symbol_cluster: str | None = None,
) -> dict[str, Any]:
    """Simulate hard-stop, trailing stop and optional horizon extension."""
    ent = pd.to_datetime(entry_ts, errors="coerce")
    exi = pd.to_datetime(planned_exit_ts, errors="coerce")
    if pd.isna(ent) or pd.isna(exi) or exi <= ent:
        return _empty_result(
            entry_price_hint=float(entry_price_hint),
            planned_exit_ts=exi,
            reason="no_intrabar_data",
        )
    if bars.empty:
        return _empty_result(
            entry_price_hint=float(entry_price_hint),
            planned_exit_ts=exi,
            reason="no_intrabar_data",
        )

    b = bars.copy()
    b["datetime"] = pd.to_datetime(b["datetime"], errors="coerce")
    b = b.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    b = b.loc[b["datetime"] >= ent].copy()
    if b.empty:
        return _empty_result(
            entry_price_hint=float(entry_price_hint),
            planned_exit_ts=exi,
            reason="no_intrabar_data",
        )
    first = b.iloc[0]
    entry_fill_dt = pd.Timestamp(first["datetime"])
    entry_fill_price = float(entry_price_hint) if np.isfinite(entry_price_hint) else float(
        pd.to_numeric(pd.Series([first.get("open", np.nan)]), errors="coerce").iloc[0]
    )
    if not np.isfinite(entry_fill_price) or entry_fill_price <= 0:
        entry_fill_price = float(pd.to_numeric(pd.Series([first.get("close", np.nan)]), errors="coerce").iloc[0])
    if not np.isfinite(entry_fill_price) or entry_fill_price <= 0:
        return _empty_result(
            entry_price_hint=float(entry_price_hint),
            planned_exit_ts=exi,
            reason="invalid_entry_price",
        )

    interval_params = _resolve_interval_params(cfg, interval)
    side_l = str(side).strip().lower()
    stop_pct = max(float(stop_loss_pct), 0.0)
    if side_l == "short":
        hard_stop = entry_fill_price * (1.0 + stop_pct)
        trail_stop = float("inf")
    else:
        hard_stop = entry_fill_price * (1.0 - stop_pct)
        trail_stop = float("-inf")

    trailing_activated = False
    running_high = float(entry_fill_price)
    running_low = float(entry_fill_price)
    trailing_allowed = (
        bool(cfg.enabled)
        and _is_trailing_regime(cfg, regime_label)
        and atr_pct_at_entry is not None
        and np.isfinite(float(atr_pct_at_entry))
        and float(atr_pct_at_entry) > 0.0
    )
    atr_abs = float(entry_fill_price * float(atr_pct_at_entry)) if trailing_allowed else float("nan")
    # trailing_take_profit / profit_aware_horizon 功能已于 2026-05-29 删除；
    # 以下三个变量保留为中性常量，仅为兼容 trade schema 输出列。
    trailing_tp_active = 0
    trailing_tp_highwater = float("nan")
    base_holding_bars = max(1, int((b["datetime"] <= exi).sum()))
    horizon_extended_to = int(base_holding_bars)

    extensions_used = 0
    max_extensions = int(horizon_cfg.max_extensions) if horizon_cfg is not None else 0
    extension_bars = int(horizon_cfg.extension_bars) if horizon_cfg is not None else 0
    if horizon_cfg is not None and bool(horizon_cfg.use_model_recommendation):
        hold_score = float(hold_extend_score) if hold_extend_score is not None else float("nan")
        if not np.isfinite(hold_score):
            logger.warning(
                "simulate_trailing_exit: hold_extend_score missing, fallback to legacy horizon extension defaults"
            )
        elif hold_score < float(horizon_cfg.min_hold_extend_score):
            max_extensions = 0
        else:
            rec = int(recommended_extension_bars) if recommended_extension_bars is not None else 0
            if rec > 0:
                extension_bars = min(int(horizon_cfg.max_model_extension_bars), rec)
            extension_bars = max(1, int(extension_bars))

    stop_hit_dt = pd.NaT
    stop_hit_price = float("nan")
    final_exit_dt = exi
    final_exit_price = float("nan")
    exit_reason = "horizon_exit"
    stop_triggered = 0
    planned_exit_price = float("nan")
    idx = 0
    horizon_end = pd.Timestamp(exi)
    def _price_return(exit_price: float) -> float:
        if not np.isfinite(exit_price) or exit_price <= 0.0:
            return float("nan")
        if side_l == "short":
            return float((entry_fill_price - exit_price) / entry_fill_price)
        return float((exit_price - entry_fill_price) / entry_fill_price)

    while idx < len(b):
        row = b.iloc[idx]
        bar_dt = pd.Timestamp(row["datetime"])
        if bar_dt > horizon_end:
            break
        bar_open = float(pd.to_numeric(pd.Series([row.get("open", np.nan)]), errors="coerce").iloc[0])
        bar_high = float(pd.to_numeric(pd.Series([row.get("high", np.nan)]), errors="coerce").iloc[0])
        bar_low = float(pd.to_numeric(pd.Series([row.get("low", np.nan)]), errors="coerce").iloc[0])
        bar_close = float(pd.to_numeric(pd.Series([row.get("close", np.nan)]), errors="coerce").iloc[0])
        planned_exit_price = bar_close if np.isfinite(bar_close) else planned_exit_price

        # Entry bar uses entry fill only; avoid peeking into intrabar high/low before entry.
        if bar_dt > entry_fill_dt:
            if np.isfinite(bar_high):
                running_high = max(running_high, bar_high)
            if np.isfinite(bar_low):
                running_low = min(running_low, bar_low)

        if trailing_allowed and np.isfinite(atr_abs) and atr_abs > 0.0:
            if side_l == "short":
                profit_atr = (entry_fill_price - running_low) / atr_abs
                if profit_atr >= float(interval_params.activation_profit_atr):
                    trailing_activated = True
                    new_stop = running_low + float(interval_params.atr_multiplier) * atr_abs
                    trail_stop = min(trail_stop, float(new_stop)) if cfg.update_only_in_favor else float(new_stop)
            else:
                profit_atr = (running_high - entry_fill_price) / atr_abs
                if profit_atr >= float(interval_params.activation_profit_atr):
                    trailing_activated = True
                    new_stop = running_high - float(interval_params.atr_multiplier) * atr_abs
                    trail_stop = max(trail_stop, float(new_stop)) if cfg.update_only_in_favor else float(new_stop)

        cur_stop = _effective_stop_for_side(side_l, hard_stop, trail_stop)
        if side_l == "short":
            if np.isfinite(bar_high) and bar_high >= cur_stop:
                stop_triggered = 1
                stop_hit_dt = bar_dt
                stop_hit_price = max(cur_stop, bar_open) if np.isfinite(bar_open) else cur_stop
                final_exit_dt = bar_dt
                final_exit_price = stop_hit_price
                exit_reason = "trailing_stop" if trailing_activated else "hard_stop"
                break
        else:
            if np.isfinite(bar_low) and bar_low <= cur_stop:
                stop_triggered = 1
                stop_hit_dt = bar_dt
                stop_hit_price = min(cur_stop, bar_open) if np.isfinite(bar_open) else cur_stop
                final_exit_dt = bar_dt
                final_exit_price = stop_hit_price
                exit_reason = "trailing_stop" if trailing_activated else "hard_stop"
                break

        # P0 fix（2026-05-24）：trailing TP 必须在 hard_stop / trailing_stop 检查 **之后**
        # 评估（设计文档 §3.4 串联顺序：hard_stop → trailing_take_profit → trailing_stop → horizon_exit）。
        # 之前的顺序错误把 trailing TP 放在最前面 → 用 bar_close 抢先于 intrabar
        # bar_low/bar_high 击穿 hard_stop，是典型的 same-bar lookahead，会过度乐观。
        # Horizon extend: only when still in extension regime and data beyond horizon is available.
        if (
            bar_dt >= horizon_end
            and _is_extend_regime(horizon_cfg, regime_label)
            and extensions_used < max_extensions
            and extension_bars > 0
        ):
            new_idx = min(len(b) - 1, idx + extension_bars)
            if new_idx > idx:
                horizon_end = pd.Timestamp(b.iloc[new_idx]["datetime"])
                extensions_used += 1
                idx += 1
                continue

        idx += 1

    if stop_triggered == 0:
        final_candidates = b.loc[b["datetime"] <= horizon_end]
        if not final_candidates.empty:
            last = final_candidates.iloc[-1]
            final_exit_dt = pd.Timestamp(last["datetime"])
            final_exit_price = float(pd.to_numeric(pd.Series([last.get("close", np.nan)]), errors="coerce").iloc[0])
        else:
            final_exit_dt = horizon_end
            final_exit_price = planned_exit_price

    price_ret = _price_return(final_exit_price)

    return {
        "entry_fill_datetime": entry_fill_dt,
        "entry_fill_price": float(entry_fill_price),
        "planned_exit_datetime": exi,
        "planned_exit_price": float(planned_exit_price),
        "stop_loss_price": float(_effective_stop_for_side(side_l, hard_stop, trail_stop)),
        "stop_hit_datetime": stop_hit_dt,
        "stop_hit_price": float(stop_hit_price),
        "stop_triggered": int(stop_triggered),
        "final_exit_datetime": pd.Timestamp(final_exit_dt),
        "final_exit_price": float(final_exit_price),
        "exit_reason": str(exit_reason),
        "price_return_pct": float(price_ret) if np.isfinite(price_ret) else float("nan"),
        "trailing_activated": int(trailing_activated),
        "trailing_stop_price": float(_effective_stop_for_side(side_l, hard_stop, trail_stop)),
        "extensions_used": int(extensions_used),
        "trailing_tp_active": int(trailing_tp_active),
        "trailing_tp_highwater": float(trailing_tp_highwater) if np.isfinite(trailing_tp_highwater) else float("nan"),
        "horizon_extended_to": int(horizon_extended_to),
    }


__all__ = ["TrailingExitSimulator", "simulate_trailing_exit"]
