"""Intrabar simulation helpers for OOT evaluation."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.config.skill_tight_range_breakout_config import BacktestConfig
from cta.strategy.skill_tight_range_backtest import load_bars, normalize_interval


class _IntrabarBarCache:
    """Lazy cache for intrabar bar frames keyed by (symbol, exchange, interval)."""

    def __init__(self, interval: str, date_span_by_symbol: dict[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]]) -> None:
        self.interval = normalize_interval(interval)
        self.date_span_by_symbol = date_span_by_symbol
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def load(self, symbol: str, exchange: str) -> pd.DataFrame:
        key = (str(symbol).upper(), str(exchange).upper())
        if key in self._cache:
            return self._cache[key]
        span = self.date_span_by_symbol.get(key)
        if span is None:
            self._cache[key] = pd.DataFrame()
            return self._cache[key]
        start_ts, end_ts = span
        start_date = str(start_ts.normalize().date())
        end_date = str((end_ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)))
        cfg = BacktestConfig(interval=self.interval)
        try:
            bars = load_bars(
                symbol=key[0],
                backtest_cfg=cfg,
                start_date=start_date,
                end_date=end_date,
                exchange=key[1],
            )
        except Exception:
            bars = pd.DataFrame()
        if not bars.empty:
            bars = bars.copy()
            bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
            bars = bars.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
        self._cache[key] = bars
        return bars

    def slice(
        self,
        symbol: str,
        exchange: str,
        start_ts: pd.Timestamp,
        end_ts: pd.Timestamp,
    ) -> pd.DataFrame:
        bars = self.load(symbol, exchange)
        if bars.empty:
            return bars
        mask = (
            (bars["datetime"] >= pd.Timestamp(start_ts))
            & (bars["datetime"] <= pd.Timestamp(end_ts))
        )
        out = bars.loc[mask].copy()
        return out.reset_index(drop=True)


def _simulate_intrabar_exit(
    *,
    side: str,
    entry_ts: pd.Timestamp,
    planned_exit_ts: pd.Timestamp,
    entry_price_hint: float,
    stop_loss_pct: float,
    bars: pd.DataFrame,
) -> dict[str, Any]:
    """Simulate entry/exit on intrabar path with hard stop tracking."""
    if pd.isna(entry_ts) or pd.isna(planned_exit_ts):
        return {
            "entry_fill_datetime": pd.NaT,
            "entry_fill_price": float("nan"),
            "planned_exit_datetime": planned_exit_ts,
            "planned_exit_price": float("nan"),
            "stop_loss_price": float("nan"),
            "stop_hit_datetime": pd.NaT,
            "stop_hit_price": float("nan"),
            "stop_triggered": 0,
            "final_exit_datetime": planned_exit_ts,
            "final_exit_price": float("nan"),
            "exit_reason": "no_intrabar_data",
            "price_return_pct": float("nan"),
        }

    side_l = str(side).strip().lower()
    if bars.empty:
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
            "exit_reason": "no_intrabar_data",
            "price_return_pct": float("nan"),
        }

    b = bars.copy()
    b["datetime"] = pd.to_datetime(b["datetime"], errors="coerce")
    b = b.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    b = b.loc[(b["datetime"] >= entry_ts) & (b["datetime"] <= planned_exit_ts)].copy()
    if b.empty:
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
            "exit_reason": "no_intrabar_data",
            "price_return_pct": float("nan"),
        }

    first = b.iloc[0]
    entry_fill_dt = pd.Timestamp(first["datetime"])
    entry_fill_price = float(entry_price_hint) if np.isfinite(entry_price_hint) else float(pd.to_numeric(pd.Series([first.get("open", np.nan)]), errors="coerce").iloc[0])
    if not np.isfinite(entry_fill_price) or entry_fill_price <= 0:
        entry_fill_price = float(pd.to_numeric(pd.Series([first.get("close", np.nan)]), errors="coerce").iloc[0])
    if not np.isfinite(entry_fill_price) or entry_fill_price <= 0:
        return {
            "entry_fill_datetime": entry_fill_dt,
            "entry_fill_price": float("nan"),
            "planned_exit_datetime": planned_exit_ts,
            "planned_exit_price": float("nan"),
            "stop_loss_price": float("nan"),
            "stop_hit_datetime": pd.NaT,
            "stop_hit_price": float("nan"),
            "stop_triggered": 0,
            "final_exit_datetime": planned_exit_ts,
            "final_exit_price": float("nan"),
            "exit_reason": "invalid_entry_price",
            "price_return_pct": float("nan"),
        }

    stop_pct = max(float(stop_loss_pct), 0.0)
    if side_l == "short":
        stop_loss_price = entry_fill_price * (1.0 + stop_pct)
    else:
        stop_loss_price = entry_fill_price * (1.0 - stop_pct)

    planned_exit_dt = pd.Timestamp(planned_exit_ts)
    planned_exit_price = float(pd.to_numeric(pd.Series([b.iloc[-1].get("close", np.nan)]), errors="coerce").iloc[0])

    stop_hit_dt = pd.NaT
    stop_hit_price = float("nan")
    final_exit_dt = planned_exit_dt
    final_exit_price = planned_exit_price
    exit_reason = "horizon_exit"
    stop_triggered = 0

    for _, row in b.iterrows():
        bar_dt = pd.Timestamp(row["datetime"])
        bar_open = float(pd.to_numeric(pd.Series([row.get("open", np.nan)]), errors="coerce").iloc[0])
        bar_high = float(pd.to_numeric(pd.Series([row.get("high", np.nan)]), errors="coerce").iloc[0])
        bar_low = float(pd.to_numeric(pd.Series([row.get("low", np.nan)]), errors="coerce").iloc[0])
        if side_l == "short":
            if np.isfinite(bar_high) and bar_high >= stop_loss_price:
                stop_triggered = 1
                stop_hit_dt = bar_dt
                stop_hit_price = max(stop_loss_price, bar_open) if np.isfinite(bar_open) else stop_loss_price
                final_exit_dt = bar_dt
                final_exit_price = stop_hit_price
                exit_reason = "stop_loss"
                break
        else:
            if np.isfinite(bar_low) and bar_low <= stop_loss_price:
                stop_triggered = 1
                stop_hit_dt = bar_dt
                stop_hit_price = min(stop_loss_price, bar_open) if np.isfinite(bar_open) else stop_loss_price
                final_exit_dt = bar_dt
                final_exit_price = stop_hit_price
                exit_reason = "stop_loss"
                break

    if not np.isfinite(final_exit_price) or final_exit_price <= 0:
        price_ret = float("nan")
    else:
        if side_l == "short":
            price_ret = (entry_fill_price - final_exit_price) / entry_fill_price
        else:
            price_ret = (final_exit_price - entry_fill_price) / entry_fill_price

    return {
        "entry_fill_datetime": entry_fill_dt,
        "entry_fill_price": entry_fill_price,
        "planned_exit_datetime": planned_exit_dt,
        "planned_exit_price": planned_exit_price,
        "stop_loss_price": stop_loss_price,
        "stop_hit_datetime": stop_hit_dt,
        "stop_hit_price": stop_hit_price,
        "stop_triggered": int(stop_triggered),
        "final_exit_datetime": final_exit_dt,
        "final_exit_price": final_exit_price,
        "exit_reason": exit_reason,
        "price_return_pct": float(price_ret) if np.isfinite(price_ret) else float("nan"),
    }


__all__ = ["_IntrabarBarCache", "_simulate_intrabar_exit"]

