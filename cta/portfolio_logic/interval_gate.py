"""Higher-timeframe (HTF) gating for candidate opportunities."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

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
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Return HTF state map keyed by (symbol, exchange)."""
        as_of_ts = pd.Timestamp(as_of)
        latest_by_interval: dict[str, pd.DataFrame] = {}
        normalized_predictions = {
            normalize_portfolio_interval(key): value
            for key, value in htf_predictions.items()
        }
        for interval in self.cfg.htf_intervals:
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
        for interval in self.cfg.htf_intervals:
            df = latest_by_interval.get(normalize_portfolio_interval(interval), pd.DataFrame())
            if df.empty:
                continue
            keys |= set(zip(df["symbol"], df["exchange"]))

        result: dict[tuple[str, str], dict[str, Any]] = {}
        for key in sorted(keys):
            regimes: list[str] = []
            computed_at_by_interval: dict[str, pd.Timestamp] = {}
            missing_interval = False
            for interval in self.cfg.htf_intervals:
                interval_key = normalize_portfolio_interval(interval)
                dfi = latest_by_interval.get(interval_key, pd.DataFrame())
                if dfi.empty:
                    missing_interval = True
                    continue
                hit = dfi.loc[(dfi["symbol"] == key[0]) & (dfi["exchange"] == key[1])]
                if hit.empty:
                    missing_interval = True
                    continue
                row = hit.iloc[-1]
                regimes.append(str(row["regime"]))
                computed_at_by_interval[interval_key] = pd.Timestamp(row["datetime"])
            if self.cfg.require_consensus and missing_interval:
                continue
            state = self._state_from_regimes(regimes)
            if not computed_at_by_interval:
                continue
            result[key] = {
                "state": state,
                "computed_at": max(computed_at_by_interval.values()),
                "computed_at_by_interval": computed_at_by_interval,
            }
        return result

    def is_state_fresh(self, entry: dict[str, Any], current_time: pd.Timestamp, interval: str) -> bool:
        """Check whether HTF state is within TTL."""
        current_ts = pd.Timestamp(current_time)
        per_interval = entry.get("computed_at_by_interval")
        if isinstance(per_interval, dict) and per_interval:
            for htf_interval, ts in per_interval.items():
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

        for _, row in out.iterrows():
            key = (str(row["symbol"]).upper(), str(row["exchange"]).upper())
            direction = self._direction_of_row(row)
            interval = str(row.get("interval", "") or "")
            entry = htf_state.get(key)

            if entry is None or not self.is_state_fresh(entry, current_ts, interval=interval):
                if self.cfg.fallback_when_htf_missing == "both":
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
