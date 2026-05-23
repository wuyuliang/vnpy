"""Cross-sectional momentum rotation strategy entry point.

入口类 ``CrossSectionalMomentumRotation`` 在 rebalance 日对 universe 做截面排名，
输出 long/short candidate DataFrame。默认 off，按 (cluster, interval) 灰度启用。

依赖：
- 配置：[cross_sectional_rotation_config.py](../config/cross_sectional_rotation_config.py)
- 排名核心：[feature/cross_sectional_rank.py](../feature/cross_sectional_rank.py)

设计文档：cta/docs/cross_sectional_momentum_rotation_design.md
"""
from __future__ import annotations

import logging
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.feature.cross_sectional_rank import (
    attach_target_weights,
    compute_cross_sectional_momentum,
    rank_within_cluster,
    rank_within_universe,
)

logger = logging.getLogger(__name__)

SIGNAL_TYPE = "cross_sectional_momentum"

# rollover_check(symbol, as_of, window_days) -> bool
RolloverCheck = Callable[[str, pd.Timestamp, int], bool]


def _is_rebalance_day(
    date: pd.Timestamp,
    *,
    rebalance_weekday: int,
    last_rebalance_dt: pd.Timestamp | None,
    max_holding_days: int,
) -> bool:
    """rebalance trigger：到指定 weekday 或距上一次 rebalance 已 ≥ max_holding_days。"""
    weekday = pd.Timestamp(date).weekday()
    if weekday == int(rebalance_weekday):
        return True
    if last_rebalance_dt is None:
        return False
    days_since = (pd.Timestamp(date) - pd.Timestamp(last_rebalance_dt)).days
    return days_since >= int(max_holding_days)


def _filter_disabled_and_rollover(
    universe_bars: Mapping[str, pd.DataFrame],
    *,
    as_of: pd.Timestamp,
    disabled_symbols: set[str] | None,
    rollover_check: RolloverCheck | None,
    exclude_window_days: int,
) -> dict[str, pd.DataFrame]:
    """剔除黑名单 + rollover 窗口内的品种。"""
    out: dict[str, pd.DataFrame] = {}
    disabled = {str(s).upper() for s in (disabled_symbols or set())}
    for symbol, bars in universe_bars.items():
        u = str(symbol).upper()
        if u in disabled:
            continue
        if rollover_check is not None:
            try:
                if bool(rollover_check(u, pd.Timestamp(as_of), int(exclude_window_days))):
                    continue
            except Exception as exc:
                logger.warning("rollover_check failed for %s @ %s: %s", u, as_of, exc)
        out[symbol] = bars
    return out


def _last_close(bars: pd.DataFrame | None) -> float:
    if bars is None or bars.empty or "close" not in bars.columns:
        return float("nan")
    close = pd.to_numeric(bars["close"], errors="coerce").dropna()
    return float(close.iloc[-1]) if not close.empty else float("nan")


class CrossSectionalMomentumRotation:
    """截面动量轮动入口。"""

    def __init__(self, cfg: CrossSectionalRotationConfig) -> None:
        self.cfg = cfg

    def generate_rebalance_candidates(
        self,
        date: pd.Timestamp,
        universe_bars: Mapping[str, pd.DataFrame],
        *,
        interval: str = "day",
        last_rebalance_dt: pd.Timestamp | None = None,
        current_drawdown_pct: float = 0.0,
        disabled_symbols: set[str] | None = None,
        rollover_check: RolloverCheck | None = None,
    ) -> pd.DataFrame:
        """rebalance 日生成 long/short candidate 表；其他日返回空。"""
        if not bool(self.cfg.use_cross_sectional_momentum_rotation):
            return pd.DataFrame()

        clusters_in_universe = {infer_symbol_cluster(s) for s in universe_bars.keys()}
        if not any(self.cfg.is_enabled(c, interval) for c in clusters_in_universe):
            return pd.DataFrame()

        if float(current_drawdown_pct) >= float(self.cfg.kill_switch_dd_pct):
            logger.info(
                "rotation kill-switch active: drawdown=%.3f >= %.3f",
                float(current_drawdown_pct), float(self.cfg.kill_switch_dd_pct),
            )
            return pd.DataFrame()

        if not _is_rebalance_day(
            date,
            rebalance_weekday=self.cfg.rebalance_weekday,
            last_rebalance_dt=last_rebalance_dt,
            max_holding_days=self.cfg.max_holding_days,
        ):
            return pd.DataFrame()

        filtered = _filter_disabled_and_rollover(
            universe_bars,
            as_of=date,
            disabled_symbols=disabled_symbols,
            rollover_check=rollover_check,
            exclude_window_days=self.cfg.exclude_within_rollover_window_days,
        )
        if not filtered:
            return pd.DataFrame()

        cluster_whitelist = set(self.cfg.universe_clusters)
        if cluster_whitelist:
            filtered = {
                s: b for s, b in filtered.items()
                if infer_symbol_cluster(s) in cluster_whitelist
            }
        if not filtered:
            return pd.DataFrame()

        momentum_df = compute_cross_sectional_momentum(
            filtered,
            lookback_days=self.cfg.lookback_days,
            skip_recent_days=self.cfg.skip_recent_days,
            as_of=pd.Timestamp(date),
            realized_vol_window_days=self.cfg.realized_vol_window_days,
        )

        if bool(self.cfg.cluster_neutral):
            ranked = rank_within_cluster(momentum_df, cfg=self.cfg)
        else:
            ranked = rank_within_universe(momentum_df, cfg=self.cfg)

        ranked = attach_target_weights(ranked, cfg=self.cfg)
        selected = ranked.loc[ranked["selected_side"].isin(["long", "short"])]
        if selected.empty:
            return pd.DataFrame()

        signal_dt = pd.Timestamp(date)
        entry_dt = signal_dt + pd.Timedelta(days=1)
        planned_exit_dt = entry_dt + pd.Timedelta(days=int(self.cfg.max_holding_days))

        # 把可选列预读出来，避免 row.get 跨版本差异
        has_uni_pct = "percentile_in_universe" in ranked.columns
        has_cls_pct = "percentile_in_cluster" in ranked.columns

        rows: list[dict[str, object]] = []
        for _, row in selected.iterrows():
            symbol = str(row["symbol"])
            cluster = str(row["cluster"])
            side = str(row["selected_side"])
            last_close = _last_close(filtered.get(symbol))
            if not np.isfinite(last_close):
                continue
            stop_price = (
                last_close * (1.0 - float(self.cfg.stop_loss_pct))
                if side == "long"
                else last_close * (1.0 + float(self.cfg.stop_loss_pct))
            )
            rows.append(
                {
                    "symbol": symbol,
                    "cluster": cluster,
                    "side": side,
                    "signal_type": SIGNAL_TYPE,
                    "signal_datetime": signal_dt,
                    "entry_datetime": entry_dt,
                    "planned_exit_datetime": planned_exit_dt,
                    "entry_price_hint": float(last_close),
                    "stop_price": float(stop_price),
                    "momentum_score": float(row["momentum_score"]),
                    "momentum_rank": float(row.get("momentum_rank", float("nan"))),
                    "percentile_in_universe": (
                        float(row.get("percentile_in_universe", float("nan")))
                        if has_uni_pct else float("nan")
                    ),
                    "percentile_in_cluster": (
                        float(row.get("percentile_in_cluster", float("nan")))
                        if has_cls_pct else float("nan")
                    ),
                    "vol_target_scale": float(row.get("vol_target_scale", 1.0)),
                    "target_weight": float(row["target_weight"]),
                    "realized_vol": float(row.get("realized_vol", float("nan"))),
                }
            )
        return pd.DataFrame(rows)


__all__ = ["CrossSectionalMomentumRotation", "SIGNAL_TYPE"]
