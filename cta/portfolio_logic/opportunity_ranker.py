"""Opportunity scoring and capacity-aware allocation."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.portfolio_logic.config import (
    CapsConfig,
    IntervalGateConfig,
    OpportunityRankerConfig,
    normalize_portfolio_interval,
)
from cta.portfolio_logic.portfolio_state import PortfolioState


class OpportunityRanker:
    """Score candidates then allocate by portfolio caps."""
    _MONEY_EPS = 0.01

    def __init__(
        self,
        cfg: OpportunityRankerConfig,
        edge_stats: dict[tuple[str, str], tuple[float, float]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.edge_stats = edge_stats or {}
        self.interval_rank_weight = IntervalGateConfig().interval_rank

    @staticmethod
    def _cluster_of_row(row: pd.Series) -> str:
        if "cluster_name" in row and str(row["cluster_name"]).strip():
            return str(row["cluster_name"]).strip()
        if "cluster" in row and str(row["cluster"]).strip():
            return str(row["cluster"]).strip()
        return infer_symbol_cluster(str(row.get("symbol", "")))

    def _edge_mu_std(self, cluster: str, interval: str) -> tuple[float, float]:
        interval_key = normalize_portfolio_interval(interval)
        mu, std = self.edge_stats.get(
            (str(cluster), interval_key),
            self.edge_stats.get((str(cluster), str(interval)), (0.0, 1.0)),
        )
        if not np.isfinite(std) or float(std) <= 1e-12:
            std = 1.0
        return float(mu), float(std)

    @staticmethod
    def _alignment_bonus(val: str) -> float:
        x = str(val).strip().lower()
        if x == "aligned":
            return 1.0
        if x == "opposite":
            return 0.0
        return 0.5

    def score(self, df: pd.DataFrame, htf_state: dict[Any, Any]) -> pd.DataFrame:
        """Append score + score components."""
        if df.empty:
            out = df.copy()
            out["score"] = pd.Series(dtype=float)
            out["score_components"] = pd.Series(dtype=object)
            return out

        out = df.copy()
        scores: list[float] = []
        components: list[dict[str, float]] = []
        lo, hi = self.cfg.edge_clip
        clip_span = float(hi - lo) if float(hi - lo) > 0 else 1.0
        for _, row in out.iterrows():
            cluster = self._cluster_of_row(row)
            interval = normalize_portfolio_interval(row.get("interval", ""))
            prob_pctl = float(pd.to_numeric(pd.Series([row.get("trade_filter_prob_pctl", np.nan)]), errors="coerce").iloc[0])
            if np.isfinite(prob_pctl):
                prob_norm = float(np.clip(prob_pctl / 100.0, 0.0, 1.0))
            else:
                prob_norm = 0.0

            mfe = float(pd.to_numeric(pd.Series([row.get("pred_mfe_atr", row.get("future_mfe_atr", np.nan))]), errors="coerce").iloc[0])
            mae = float(pd.to_numeric(pd.Series([row.get("pred_mae_atr", row.get("future_mae_atr", np.nan))]), errors="coerce").iloc[0])
            raw_edge = 0.0
            if np.isfinite(mfe) and np.isfinite(mae):
                raw_edge = float(mfe - 0.7 * mae)
            mu, std = self._edge_mu_std(cluster, interval)
            edge_z = float(np.clip((raw_edge - mu) / std, lo, hi))
            edge_norm = float((edge_z - lo) / clip_span)

            rank_w = float(self.interval_rank_weight.get(interval, 0.25))
            align = self._alignment_bonus(str(row.get("htf_alignment", "neutral")))
            score = (
                self.cfg.w_prob * prob_norm
                + self.cfg.w_edge * edge_norm
                + self.cfg.w_rank * rank_w
                + self.cfg.w_align * align
            )
            scores.append(float(score))
            components.append(
                {
                    "prob_norm": prob_norm,
                    "edge_norm": edge_norm,
                    "rank_weight": rank_w,
                    "align_bonus": align,
                }
            )

        out["score"] = scores
        out["score_components"] = components
        return out

    def _base_notional(self, row: pd.Series, state: PortfolioState) -> float:
        for col in ("base_notional", "allocated_notional_hint"):
            if col in row.index:
                val = float(pd.to_numeric(pd.Series([row.get(col, np.nan)]), errors="coerce").iloc[0])
                if np.isfinite(val) and val > 0.0:
                    return val
        return float(state.equity) * float(self.cfg.base_notional_pct)

    @classmethod
    def _enforce_notional_caps(
        cls,
        sym_key: tuple[str, str],
        cluster: str,
        notional: float,
        state: PortfolioState,
        caps: CapsConfig,
    ) -> float:
        if notional <= 0.0:
            return 0.0
        symbol_cap = float(caps.max_symbol_notional_pct) * float(state.equity)
        cluster_cap = float(caps.max_cluster_notional_pct) * float(state.equity)
        total_cap = float(caps.max_total_notional_pct) * float(state.equity)

        def _room(cap: float, used: float) -> float:
            rem = float(cap) - float(used)
            if abs(rem) < cls._MONEY_EPS:
                return 0.0
            return max(0.0, rem)

        room_symbol = _room(symbol_cap, state.tentative_symbol_notional(sym_key))
        room_cluster = _room(cluster_cap, state.tentative_cluster_notional(cluster))
        room_total = _room(total_cap, state.tentative_total_notional())
        allowed = min(float(notional), float(room_symbol), float(room_cluster), float(room_total))
        if float(allowed) < cls._MONEY_EPS:
            return 0.0
        return max(0.0, float(allowed))

    def allocate(
        self,
        df_scored: pd.DataFrame,
        state: PortfolioState,
        caps: CapsConfig,
        score_threshold: float,
        min_prob_pctl: float = 0.0,
    ) -> pd.DataFrame:
        """Pick top opportunities under count/notional caps."""
        if not (0.0 < float(score_threshold) < 1.0):
            raise ValueError(f"score_threshold must be in (0,1), got {score_threshold}")
        if not (0.0 <= float(min_prob_pctl) <= 100.0):
            raise ValueError(f"min_prob_pctl must be in [0,100], got {min_prob_pctl}")
        cols = list(df_scored.columns)
        if "allocated_notional" not in cols:
            cols.append("allocated_notional")
        if df_scored.empty:
            return pd.DataFrame(columns=cols)
        state.begin_allocation()

        ordered = df_scored.sort_values("score", ascending=False).reset_index(drop=True)
        picks: list[dict[str, Any]] = []
        for _, row in ordered.iterrows():
            score = float(pd.to_numeric(pd.Series([row.get("score", np.nan)]), errors="coerce").iloc[0])
            if not np.isfinite(score) or score < float(score_threshold):
                continue
            raw_pctl = pd.to_numeric(
                pd.Series([row.get("trade_filter_prob_pctl", np.nan)]),
                errors="coerce",
            ).iloc[0]
            if not np.isfinite(raw_pctl):
                raw_prob = pd.to_numeric(
                    pd.Series([row.get("trade_filter_prob", np.nan)]),
                    errors="coerce",
                ).iloc[0]
                raw_pctl = float(np.clip(float(raw_prob) * 100.0, 0.0, 100.0)) if np.isfinite(raw_prob) else float("nan")
            if np.isfinite(raw_pctl) and float(raw_pctl) < float(min_prob_pctl):
                continue

            symbol = str(row.get("symbol", "")).upper()
            exchange = str(row.get("exchange", "")).upper()
            sym_key = (symbol, exchange)
            direction = str(row.get("direction", row.get("side", "long"))).lower()
            cluster = self._cluster_of_row(row)

            if caps.dedup_same_symbol_same_direction and state.has_open_or_picked(sym_key, direction):
                continue
            if state.tentative_total_positions() >= int(caps.max_total_positions):
                break
            if state.tentative_cluster_count(cluster) >= int(caps.max_total_per_cluster):
                continue
            if state.tentative_symbol_count(sym_key) >= int(caps.max_per_symbol):
                continue

            notional = self._base_notional(row, state)
            notional = self._enforce_notional_caps(sym_key, cluster, notional, state, caps)
            if notional <= 0.0:
                continue

            picked = dict(row)
            picked["allocated_notional"] = float(notional)
            picks.append(picked)
            state.tentative_apply(sym_key, cluster, notional, direction)
        state.commit_allocation()
        if not picks:
            return pd.DataFrame(columns=cols)
        out = pd.DataFrame(picks)
        if "allocated_notional" not in out.columns:
            out["allocated_notional"] = pd.Series(dtype=float)
        return out
