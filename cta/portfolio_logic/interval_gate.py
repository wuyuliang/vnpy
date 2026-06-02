"""Higher-timeframe (HTF) gating for candidate opportunities."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.portfolio_logic.config import IntervalGateConfig, normalize_portfolio_interval
from cta.model.oot.block_reasons import BR_HTF_CONFLICT, BR_HTF_MISSING, BR_HTF_OPPOSITE, BR_HTF_UNKNOWN

logger = logging.getLogger(__name__)

_UP_REGIMES = {"trend_up", "bull", "up"}
_DOWN_REGIMES = {"trend_down", "bear", "down"}
_RANGE_REGIMES = {"range", "sideways", "neutral"}


class HtfGate:
    """Compute and apply HTF directional state."""

    def __init__(self, cfg: IntervalGateConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _normalize_symbol_exchange(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "symbol" not in out.columns:
            out["symbol"] = ""
        if "exchange" not in out.columns:
            out["exchange"] = ""
        out["symbol"] = out["symbol"].astype(str).str.upper()
        out["exchange"] = out["exchange"].astype(str).str.upper()
        return out

    @staticmethod
    def _extract_regime(df: pd.DataFrame) -> pd.Series:
        for col in ("pred_regime_label", "regime_label", "regime", "pred_regime"):
            if col in df.columns:
                return df[col].astype(str).str.lower()
        return pd.Series([""] * len(df), index=df.index, dtype=object)

    @staticmethod
    def _regime_to_side_set(regime: str) -> set[str]:
        r = str(regime).strip().lower()
        if r in _UP_REGIMES:
            return {"long"}
        if r in _DOWN_REGIMES:
            return {"short"}
        if r in _RANGE_REGIMES:
            return {"long", "short"}
        return set()

    def _state_from_regimes(self, regimes: list[str]) -> str:
        if not regimes:
            return "none"
        side_sets = [self._regime_to_side_set(r) for r in regimes]
        if any(len(s) == 0 for s in side_sets):
            return "none"
        if self.cfg.require_consensus:
            sides = set.intersection(*side_sets)
        else:
            sides = set.union(*side_sets)
        if sides == {"long"}:
            return "long_only"
        if sides == {"short"}:
            return "short_only"
        if sides == {"long", "short"}:
            return "both"
        return "none"

    def compute_htf_state(
        self,
        htf_predictions: dict[str, pd.DataFrame],
        as_of: pd.Timestamp,
        htf_intervals_override: tuple[str, ...] | None = None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Return HTF state map keyed by (symbol, exchange).

        ``htf_intervals_override``（2026-06-01）：显式指定参与共识的高周期集合，覆盖
        ``cfg.htf_intervals``。统一组合多 interval 回放时，调用方按"候选自身 interval
        rank"逐组裁剪参照集——最高周期（day）只用 ``("day",)`` 自身趋势判定，避免被更低
        周期（60min/30min）的缺失或冲突误杀（"本周期及以上趋势对齐"语义）。
        """
        as_of_ts = pd.Timestamp(as_of)
        intervals = (
            tuple(htf_intervals_override)
            if htf_intervals_override is not None
            else tuple(self.cfg.htf_intervals)
        )
        latest_by_interval: dict[str, pd.DataFrame] = {}
        normalized_predictions = {
            normalize_portfolio_interval(key): value
            for key, value in htf_predictions.items()
        }
        for interval in intervals:
            interval_key = normalize_portfolio_interval(interval)
            raw = normalized_predictions.get(interval_key)
            if raw is None:
                raw = htf_predictions.get(str(interval))
            if raw is None or raw.empty:
                latest_by_interval[interval_key] = pd.DataFrame()
                continue
            df = self._normalize_symbol_exchange(raw)
            if "datetime" not in df.columns:
                latest_by_interval[interval_key] = pd.DataFrame()
                continue
            df = df.copy()
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"])
            df = df.loc[df["datetime"] <= as_of_ts]
            if df.empty:
                latest_by_interval[interval_key] = pd.DataFrame()
                continue
            df["regime"] = self._extract_regime(df)
            df = df.sort_values("datetime").drop_duplicates(subset=["symbol", "exchange"], keep="last")
            latest_by_interval[interval_key] = df[["symbol", "exchange", "datetime", "regime"]].reset_index(drop=True)

        keys: set[tuple[str, str]] = set()
        for interval in intervals:
            df = latest_by_interval.get(normalize_portfolio_interval(interval), pd.DataFrame())
            if df.empty:
                continue
            keys |= set(zip(df["symbol"], df["exchange"]))

        result: dict[tuple[str, str], dict[str, Any]] = {}
        for key in sorted(keys):
            regimes: list[str] = []
            regime_by_interval: dict[str, str] = {}
            computed_at_by_interval: dict[str, pd.Timestamp] = {}
            for interval in self.cfg.htf_intervals:
                interval_key = normalize_portfolio_interval(interval)
                dfi = latest_by_interval.get(interval_key, pd.DataFrame())
                if dfi.empty:
                    continue
                hit = dfi.loc[(dfi["symbol"] == key[0]) & (dfi["exchange"] == key[1])]
                if hit.empty:
                    continue
                row = hit.iloc[-1]
                regimes.append(str(row["regime"]))
                regime_by_interval[interval_key] = str(row["regime"])
                computed_at_by_interval[interval_key] = pd.Timestamp(row["datetime"])
            state = self._state_from_regimes(regimes)
            if not computed_at_by_interval:
                continue
            result[key] = {
                "state": state,
                "computed_at": max(computed_at_by_interval.values()),
                "computed_at_by_interval": computed_at_by_interval,
                "regime_by_interval": regime_by_interval,
            }
        return result

    def _resolve_effective_intervals(
        self,
        *,
        candidate_interval: str,
        entry: dict[str, Any],
    ) -> tuple[str, ...]:
        configured_intervals = [
            normalize_portfolio_interval(interval) for interval in self.cfg.htf_intervals
        ]
        rank_map = dict(self.cfg.interval_rank or {})
        candidate_rank = float(rank_map.get(candidate_interval, 0.0))
        intervals = [
            interval
            for interval in configured_intervals
            if float(rank_map.get(interval, 0.0)) >= candidate_rank
        ]
        if candidate_interval and candidate_interval not in intervals:
            intervals.append(candidate_interval)
        dedup_set = {normalize_portfolio_interval(interval) for interval in intervals if str(interval).strip()}
        if not dedup_set and configured_intervals:
            dedup_set = {normalize_portfolio_interval(interval) for interval in configured_intervals}
        if not dedup_set and candidate_interval:
            dedup_set = {candidate_interval}
        if candidate_interval and candidate_interval in dedup_set:
            ordered_rest = sorted(
                (interval for interval in dedup_set if interval != candidate_interval),
                key=lambda interval: float(rank_map.get(interval, 0.0)),
            )
            return tuple([candidate_interval, *ordered_rest])
        ordered = sorted(
            dedup_set,
            key=lambda interval: float(rank_map.get(interval, 0.0)),
        )
        return tuple(ordered)

    def _project_entry_for_intervals(
        self,
        *,
        entry: dict[str, Any],
        effective_intervals: tuple[str, ...],
    ) -> dict[str, Any] | None:
        regime_by_interval = entry.get("regime_by_interval")
        computed_at_by_interval = entry.get("computed_at_by_interval")
        if not isinstance(regime_by_interval, dict) or not isinstance(computed_at_by_interval, dict):
            return entry
        selected_regimes: list[str] = []
        selected_regime_by_interval: dict[str, str] = {}
        selected_ts_by_interval: dict[str, pd.Timestamp] = {}
        missing_any = False
        for interval in effective_intervals:
            key = normalize_portfolio_interval(interval)
            if key not in regime_by_interval or key not in computed_at_by_interval:
                missing_any = True
                continue
            ts = pd.Timestamp(computed_at_by_interval[key])
            if pd.isna(ts):
                missing_any = True
                continue
            regime = str(regime_by_interval[key])
            selected_regimes.append(regime)
            selected_regime_by_interval[key] = regime
            selected_ts_by_interval[key] = ts
        if self.cfg.require_consensus and missing_any:
            return None
        if not selected_ts_by_interval:
            return None
        return {
            "state": self._state_from_regimes(selected_regimes),
            "computed_at": max(selected_ts_by_interval.values()),
            "computed_at_by_interval": selected_ts_by_interval,
            "regime_by_interval": selected_regime_by_interval,
        }

    def is_state_fresh(
        self,
        entry: dict[str, Any],
        current_time: pd.Timestamp,
        interval: str,
        *,
        effective_intervals: tuple[str, ...] | None = None,
    ) -> bool:
        """Check whether HTF state is within TTL."""
        current_ts = pd.Timestamp(current_time)
        per_interval = entry.get("computed_at_by_interval")
        if isinstance(per_interval, dict) and per_interval:
            if effective_intervals:
                interval_keys = [normalize_portfolio_interval(i) for i in effective_intervals]
            else:
                interval_keys = [
                    normalize_portfolio_interval(i) for i in per_interval.keys()
                ]
            for htf_interval in interval_keys:
                if htf_interval not in per_interval:
                    return False
                ts = per_interval.get(htf_interval)
                ttl = int(self.cfg.state_ttl_seconds.get(normalize_portfolio_interval(htf_interval), 0))
                if ttl <= 0:
                    continue
                val = pd.Timestamp(ts)
                if pd.isna(val) or (current_ts - val).total_seconds() > ttl:
                    return False
            return True

        ts = pd.Timestamp(entry.get("computed_at"))
        if pd.isna(ts):
            return False
        ttl = int(self.cfg.state_ttl_seconds.get(normalize_portfolio_interval(interval), 0))
        if ttl <= 0:
            return True
        return bool((current_ts - ts).total_seconds() <= ttl)

    @staticmethod
    def _direction_of_row(row: pd.Series) -> str:
        if "direction" in row and str(row["direction"]).strip():
            return str(row["direction"]).strip().lower()
        if "side" in row and str(row["side"]).strip():
            return str(row["side"]).strip().lower()
        return "long"

    def filter(
        self,
        opportunities: pd.DataFrame,
        htf_state: dict[tuple[str, str], dict[str, Any]],
        current_time: pd.Timestamp,
    ) -> pd.DataFrame:
        """Annotate opportunities with HTF allowance and alignment."""
        if opportunities.empty:
            out = opportunities.copy()
            out["htf_allowed"] = pd.Series(dtype=bool)
            out["htf_block_reason"] = pd.Series(dtype=object)
            out["htf_alignment"] = pd.Series(dtype=object)
            return out

        out = opportunities.copy()
        if "symbol" not in out.columns:
            out["symbol"] = ""
        if "exchange" not in out.columns:
            out["exchange"] = ""
        out["symbol"] = out["symbol"].astype(str).str.upper()
        out["exchange"] = out["exchange"].astype(str).str.upper()
        allowed_list: list[bool] = []
        reason_list: list[str] = []
        align_list: list[str] = []
        current_ts = pd.Timestamp(current_time)

        per_cell_fallback = dict(
            getattr(self.cfg, "fallback_when_htf_missing_by_cluster_interval", {}) or {}
        )
        for _, row in out.iterrows():
            key = (str(row["symbol"]).upper(), str(row["exchange"]).upper())
            direction = self._direction_of_row(row)
            interval = normalize_portfolio_interval(str(row.get("interval", "") or ""))
            entry = htf_state.get(key)
            effective_intervals: tuple[str, ...] = ()
            if entry is not None:
                effective_intervals = self._resolve_effective_intervals(
                    candidate_interval=interval,
                    entry=entry,
                )
                entry = self._project_entry_for_intervals(
                    entry=entry,
                    effective_intervals=effective_intervals,
                )

            if entry is None or not self.is_state_fresh(
                entry,
                current_ts,
                interval=interval,
                effective_intervals=effective_intervals,
            ):
                # 决定 fallback 策略：先查 (cluster, interval) override，未配时用全局值
                effective_fallback = self.cfg.fallback_when_htf_missing
                if per_cell_fallback:
                    cluster = str(infer_symbol_cluster(str(row["symbol"]) or "")).lower()
                    itv_norm = normalize_portfolio_interval(interval)
                    cell_key = f"{cluster}|{itv_norm}"
                    if cell_key in per_cell_fallback:
                        effective_fallback = per_cell_fallback[cell_key]
                if effective_fallback == "both":
                    allowed_list.append(True)
                    reason_list.append("")
                    align_list.append("neutral")
                else:
                    allowed_list.append(False)
                    reason_list.append(BR_HTF_MISSING)
                    align_list.append("neutral")
                continue

            state = str(entry.get("state", "none"))
            if state == "both":
                allowed_list.append(True)
                reason_list.append("")
                align_list.append("neutral")
                continue
            if state == "none":
                allowed_list.append(False)
                reason_list.append(BR_HTF_CONFLICT)
                align_list.append("opposite")
                continue
            if state == "long_only":
                ok = direction == "long"
                allowed_list.append(ok)
                reason_list.append("" if ok else BR_HTF_OPPOSITE)
                align_list.append("aligned" if ok else "opposite")
                continue
            if state == "short_only":
                ok = direction == "short"
                allowed_list.append(ok)
                reason_list.append("" if ok else BR_HTF_OPPOSITE)
                align_list.append("aligned" if ok else "opposite")
                continue

            # Unknown state 表示 _state_from_regimes 出现了未覆盖的 case，
            # 通常意味着上游 regime label 落到了 _UP_REGIMES/_DOWN_REGIMES/
            # _RANGE_REGIMES 三个集合之外。详见 cta/docs/block_reason.md §8.3。
            logger.warning(
                "Unknown HTF state=%r for %s (entry=%s); falling back to block (htf_unknown). "
                "This indicates a bug in HtfGate._state_from_regimes — please file an issue.",
                state, key, dict(entry) if entry else None,
            )
            allowed_list.append(False)
            reason_list.append(BR_HTF_UNKNOWN)
            align_list.append("neutral")

        out["htf_allowed"] = allowed_list
        out["htf_block_reason"] = reason_list
        out["htf_alignment"] = align_list
        return out
