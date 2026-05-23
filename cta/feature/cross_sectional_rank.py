"""Cross-sectional momentum ranking helpers.

输入 universe 的 bar 数据，按 `(close[t-skip] / close[t-lookback] - 1)` 计算每个
品种的截面动量得分，并支持：
- 全局排名 (`rank_within_universe`)
- cluster-neutral 内部排名 (`rank_within_cluster`)
- vol-target 权重 (`attach_target_weights`)

设计文档：cta/docs/cross_sectional_momentum_rotation_design.md
"""
from __future__ import annotations

import logging
from typing import Mapping

import numpy as np
import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.config.symbol_cluster_config import infer_symbol_cluster

logger = logging.getLogger(__name__)


def _resolve_close_at(close: pd.Series, target_dt: pd.Timestamp) -> float:
    """返回 ≤ target_dt 的最新有效 close；找不到返回 NaN。"""
    series = close.dropna()
    if series.empty:
        return float("nan")
    available = series.loc[series.index <= target_dt]
    if available.empty:
        return float("nan")
    return float(available.iloc[-1])


def _compute_one_symbol_momentum(
    bars: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    lookback_days: int,
    skip_recent_days: int,
) -> float:
    """单品种动量得分：(close[t-skip] / close[t-lookback]) - 1"""
    if bars is None or bars.empty or "close" not in bars.columns or "datetime" not in bars.columns:
        return float("nan")
    df = bars[["datetime", "close"]].copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    if df.empty:
        return float("nan")
    close = pd.to_numeric(df["close"], errors="coerce")
    close.index = df["datetime"]

    end_dt = pd.Timestamp(as_of) - pd.Timedelta(days=int(skip_recent_days))
    start_dt = pd.Timestamp(as_of) - pd.Timedelta(days=int(lookback_days))

    end_close = _resolve_close_at(close, end_dt)
    start_close = _resolve_close_at(close, start_dt)

    if not np.isfinite(end_close) or not np.isfinite(start_close) or start_close <= 0.0:
        return float("nan")
    return float(end_close / start_close - 1.0)


def _compute_realized_vol(
    bars: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    window_days: int,
) -> float:
    """近 window_days 日 log-return 标准差。"""
    if bars is None or bars.empty or "close" not in bars.columns or "datetime" not in bars.columns:
        return float("nan")
    df = bars[["datetime", "close"]].copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    df = df.loc[df["datetime"] <= pd.Timestamp(as_of)]
    if len(df) < 2:
        return float("nan")
    tail = df.tail(int(window_days) + 1)
    log_ret = np.log(pd.to_numeric(tail["close"], errors="coerce")).diff().dropna()
    if log_ret.empty:
        return float("nan")
    return float(log_ret.std(ddof=0))


def compute_cross_sectional_momentum(
    universe_bars: Mapping[str, pd.DataFrame],
    *,
    lookback_days: int = 60,
    skip_recent_days: int = 5,
    as_of: pd.Timestamp,
    realized_vol_window_days: int | None = None,
) -> pd.DataFrame:
    """对 universe 计算 long-format 动量得分表。

    Returns DataFrame with columns:
        symbol, cluster, momentum_score, realized_vol, valid_for_ranking.
    """
    rows: list[dict[str, object]] = []
    for symbol, bars in universe_bars.items():
        score = _compute_one_symbol_momentum(
            bars,
            as_of=as_of,
            lookback_days=lookback_days,
            skip_recent_days=skip_recent_days,
        )
        rvol = (
            _compute_realized_vol(
                bars, as_of=as_of, window_days=int(realized_vol_window_days)
            )
            if realized_vol_window_days is not None and int(realized_vol_window_days) > 0
            else float("nan")
        )
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "cluster": str(infer_symbol_cluster(symbol)),
                "momentum_score": float(score),
                "realized_vol": float(rvol),
                "valid_for_ranking": bool(np.isfinite(score)),
            }
        )
    return pd.DataFrame(rows)


def _percentile_descending(values: pd.Series) -> pd.Series:
    """descending percentile：rank=1（最大）映射到 1.0，rank=n（最小）映射到 1/n。"""
    n = int(values.notna().sum())
    if n == 0:
        return pd.Series(np.nan, index=values.index, dtype=float)
    descending_rank = values.rank(method="min", ascending=False)
    return (float(n) - descending_rank + 1.0) / float(n)


def rank_within_universe(
    momentum_df: pd.DataFrame,
    *,
    cfg: CrossSectionalRotationConfig,
) -> pd.DataFrame:
    """全局排名：top/bottom quantile 内分别打 long/short 标签。"""
    df = momentum_df.copy()
    mask_valid = df["valid_for_ranking"].astype(bool)
    df["momentum_rank"] = pd.Series(np.nan, index=df.index, dtype=float)
    df["percentile_in_universe"] = pd.Series(np.nan, index=df.index, dtype=float)
    df["selected_side"] = ""

    valid_scores = df.loc[mask_valid, "momentum_score"]
    if valid_scores.empty:
        return df

    percentile = _percentile_descending(valid_scores)
    rank = valid_scores.rank(method="min", ascending=False)
    df.loc[mask_valid, "percentile_in_universe"] = percentile.values
    df.loc[mask_valid, "momentum_rank"] = rank.values

    is_long = df["percentile_in_universe"].fillna(-1.0) >= float(cfg.top_quantile)
    df.loc[is_long, "selected_side"] = "long"
    if not bool(cfg.long_only_mode):
        is_short = df["percentile_in_universe"].fillna(2.0) <= float(cfg.bottom_quantile)
        df.loc[is_short, "selected_side"] = "short"
    return df


def rank_within_cluster(
    momentum_df: pd.DataFrame,
    *,
    cfg: CrossSectionalRotationConfig,
) -> pd.DataFrame:
    """cluster 内分别排名，每个 cluster 取 top/bottom 各 N 个；cluster 太小则跳过。"""
    df = momentum_df.copy()
    df["momentum_rank"] = pd.Series(np.nan, index=df.index, dtype=float)
    df["percentile_in_cluster"] = pd.Series(np.nan, index=df.index, dtype=float)
    df["selected_side"] = ""

    for cluster, sub in df.groupby("cluster"):
        valid = sub.loc[sub["valid_for_ranking"].astype(bool)]
        n = len(valid)
        if n < int(cfg.min_cluster_size):
            continue
        valid_scores = valid["momentum_score"]
        percentile = _percentile_descending(valid_scores)
        rank = valid_scores.rank(method="min", ascending=False)
        df.loc[valid.index, "percentile_in_cluster"] = percentile.values
        df.loc[valid.index, "momentum_rank"] = rank.values

        n_long = max(1, int(round(n * float(cfg.top_quantile_in_cluster))))
        n_long = min(n_long, n)
        long_idx = rank.nsmallest(n_long).index  # rank=1 最高
        df.loc[long_idx, "selected_side"] = "long"

        if not bool(cfg.long_only_mode):
            remaining_for_short = max(0, n - n_long)
            n_short = max(1, int(round(n * float(cfg.bottom_quantile_in_cluster))))
            n_short = min(n_short, remaining_for_short)
            if n_short > 0:
                short_idx = rank.nlargest(n_short).index
                df.loc[short_idx, "selected_side"] = "short"
        logger.debug(
            "rank_within_cluster: cluster=%s n=%d long=%d short=%d",
            cluster, n, int((df.loc[valid.index, "selected_side"] == "long").sum()),
            int((df.loc[valid.index, "selected_side"] == "short").sum()),
        )
    return df


def attach_target_weights(
    ranked_df: pd.DataFrame,
    *,
    cfg: CrossSectionalRotationConfig,
) -> pd.DataFrame:
    """按 vol-target + base_weight 计算每个 selected 品种的 target_weight。"""
    df = ranked_df.copy()
    df["vol_target_scale"] = 1.0
    df["target_weight"] = 0.0

    selected = df.loc[df["selected_side"].isin(["long", "short"])]
    if selected.empty:
        return df

    if bool(cfg.use_vol_target_weighting):
        rvol = pd.to_numeric(df.loc[selected.index, "realized_vol"], errors="coerce")
        valid_vol = rvol.where(rvol > 0.0, np.nan)
        scale = float(cfg.target_vol_pct_per_symbol) / valid_vol
        df.loc[selected.index, "vol_target_scale"] = (
            scale.fillna(1.0).clip(lower=0.0, upper=10.0).values
        )

    base_weight = float(cfg.gross_exposure_target)
    long_mask = df["selected_side"] == "long"
    short_mask = df["selected_side"] == "short"
    long_count = int(long_mask.sum())
    short_count = int(short_mask.sum())

    if long_count > 0:
        per_long = base_weight / float(long_count)
        df.loc[long_mask, "target_weight"] = (
            per_long * df.loc[long_mask, "vol_target_scale"].astype(float)
        )
    if short_count > 0:
        per_short = -base_weight / float(short_count)
        df.loc[short_mask, "target_weight"] = (
            per_short * df.loc[short_mask, "vol_target_scale"].astype(float)
        )

    weight_cap = float(cfg.max_symbol_notional_pct)
    df["target_weight"] = df["target_weight"].clip(lower=-weight_cap, upper=weight_cap)
    return df


__all__ = [
    "compute_cross_sectional_momentum",
    "rank_within_universe",
    "rank_within_cluster",
    "attach_target_weights",
]
