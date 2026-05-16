"""OOT real-execution evaluation helpers."""
from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG, OotEvaluationConfig
from cta.config.skill_tight_range_breakout_config import BacktestConfig
from cta.config.symbol_cluster_config import infer_symbol_cluster, infer_symbol_roll_cost_pct
from cta.portfolio_logic.interval_gate import HtfGate
from cta.portfolio_logic.opportunity_ranker import OpportunityRanker
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.portfolio_logic.pyramid_manager import PyramidManager, PyramidPosition
from cta.portfolio_logic.risk_throttle import EquityTracker, RiskThrottle
from cta.portfolio_logic.trailing_exit import simulate_trailing_exit
from cta.portfolio_logic.config import normalize_portfolio_interval
from cta.strategy.skill_tight_range_backtest import load_bars, normalize_interval

logger = logging.getLogger(__name__)


def _max_drawdown_from_return_series(ret: pd.Series) -> float:
    values = pd.to_numeric(ret, errors="coerce").fillna(0.0)
    if values.empty:
        return float("nan")
    equity = values.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    return float(drawdown.min())


def _count_roll_dates_between(
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    roll_day_of_month: int = 14,
) -> int:
    """Count how many roll-over dates the holding period covers.

    Approximation: each month's roll-over happens at ``roll_day_of_month``
    (default 14, typical main-contract switch day for many domestic futures).
    Returns the number of such dates in (entry_ts, exit_ts].
    """
    ent = pd.to_datetime(entry_ts, errors="coerce")
    exi = pd.to_datetime(exit_ts, errors="coerce")
    if pd.isna(ent) or pd.isna(exi) or exi <= ent:
        return 0
    day = int(roll_day_of_month)
    if day < 1 or day > 28:
        day = 14
    # 枚举从 ent.month 到 exi.month 的每个月的 roll 日，统计落在 (ent, exi] 内的数量
    count = 0
    cur = pd.Timestamp(year=ent.year, month=ent.month, day=day)
    if cur <= ent:
        cur = cur + pd.DateOffset(months=1)
    while cur <= exi:
        count += 1
        cur = cur + pd.DateOffset(months=1)
    return int(count)


def _calc_roll_cost(
    *,
    symbol: str,
    notional: float,
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    cfg: OotEvaluationConfig,
) -> float:
    """Estimate roll-over cost over the holding period.

    P0.3 modeling change: 实战里 roll-over 成本只在持仓**经过换月日**时发生
    （一次性平旧月+开新月），而非按持仓总天数连续摊销。这里：
      total_cost = notional × annual_pct / 12 × n_roll_dates_in_period
    （年化 cost 摊到 12 次月度换月，每经过 1 个 roll_day_of_month 扣 1 份）。
    """
    if not bool(getattr(cfg, "use_roll_cost", False)):
        return 0.0
    if not np.isfinite(notional) or float(notional) <= 0.0:
        return 0.0
    ent = pd.to_datetime(entry_ts, errors="coerce")
    exi = pd.to_datetime(exit_ts, errors="coerce")
    if pd.isna(ent) or pd.isna(exi) or exi <= ent:
        return 0.0
    # P0.3：unknown cluster 默认 5%（与 cluster=other 一致）而非旧的 0%，
    # 防止"未知品种零成本"的乐观偏差。
    annual_pct = infer_symbol_roll_cost_pct(
        str(symbol),
        default_pct=float(getattr(cfg, "default_roll_cost_pct_per_year", 0.05) or 0.05),
    )
    if not np.isfinite(annual_pct) or float(annual_pct) <= 0.0:
        return 0.0
    roll_day = int(getattr(cfg, "roll_cost_day_of_month", 14) or 14)
    n_rolls = _count_roll_dates_between(ent, exi, roll_day_of_month=roll_day)
    if n_rolls <= 0:
        return 0.0
    # 把年化 roll cost 摊到每月 1 次 → 单次 = annual_pct / 12
    per_roll_pct = float(annual_pct) / 12.0
    return float(notional) * per_roll_pct * float(n_rolls)


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
        # minute parquet loader过滤上界用 <= Timestamp(end_date)，这里传到 23:59:59 防止丢整天数据
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
                # short stop gap-up: worst at open if open>stop
                if np.isfinite(bar_open):
                    stop_hit_price = max(stop_loss_price, bar_open)
                else:
                    stop_hit_price = stop_loss_price
                final_exit_dt = bar_dt
                final_exit_price = stop_hit_price
                exit_reason = "stop_loss"
                break
        else:
            if np.isfinite(bar_low) and bar_low <= stop_loss_price:
                stop_triggered = 1
                stop_hit_dt = bar_dt
                # long stop gap-down: worst at open if open<stop
                if np.isfinite(bar_open):
                    stop_hit_price = min(stop_loss_price, bar_open)
                else:
                    stop_hit_price = stop_loss_price
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


def _build_position_lifetime_table(trade_df: pd.DataFrame) -> pd.DataFrame:
    """Build one-row-per-position lifetime stats from trade details."""
    if trade_df.empty or "pos_id" not in trade_df.columns:
        return pd.DataFrame(
            columns=[
                "pos_id",
                "symbol",
                "exchange",
                "direction",
                "first_entry",
                "last_exit",
                "layer_count",
                "max_active_layers",
                "peak_notional",
            ]
        )
    df = trade_df.copy()
    if "execution_status" in df.columns:
        df = df.loc[df["execution_status"].astype(str) == "executed"].copy()
    df = df.loc[df["pos_id"].astype(str).str.strip() != ""].copy()
    if df.empty:
        return pd.DataFrame(
            columns=[
                "pos_id",
                "symbol",
                "exchange",
                "direction",
                "first_entry",
                "last_exit",
                "layer_count",
                "max_active_layers",
                "peak_notional",
            ]
        )
    df["entry_datetime"] = pd.to_datetime(df.get("entry_datetime", pd.NaT), errors="coerce")
    df["exit_datetime"] = pd.to_datetime(df.get("exit_datetime", pd.NaT), errors="coerce")
    df["layer_id"] = pd.to_numeric(df.get("layer_id", np.nan), errors="coerce")
    df["position_notional"] = pd.to_numeric(df.get("position_notional", np.nan), errors="coerce")

    def _active_stats(grp: pd.DataFrame) -> tuple[int, float]:
        events: list[tuple[pd.Timestamp, int, int, float]] = []
        for _, row in grp.iterrows():
            entry_ts = pd.to_datetime(row.get("entry_datetime"), errors="coerce")
            exit_ts = pd.to_datetime(row.get("exit_datetime"), errors="coerce")
            notional = pd.to_numeric(pd.Series([row.get("position_notional", np.nan)]), errors="coerce").iloc[0]
            if pd.isna(entry_ts) or pd.isna(exit_ts) or not np.isfinite(float(notional)):
                continue
            n = max(0.0, float(notional))
            events.append((pd.Timestamp(entry_ts), 1, 1, n))
            events.append((pd.Timestamp(exit_ts), 0, -1, -n))
        active_layers = 0
        active_notional = 0.0
        max_layers = 0
        peak_notional = 0.0
        for _, _, delta_layers, delta_notional in sorted(events, key=lambda x: (x[0], x[1])):
            active_layers = max(0, active_layers + int(delta_layers))
            active_notional = max(0.0, active_notional + float(delta_notional))
            max_layers = max(max_layers, active_layers)
            peak_notional = max(peak_notional, active_notional)
        return int(max_layers), float(peak_notional)

    parts: list[dict[str, Any]] = []
    for pos_id, grp in df.groupby("pos_id", dropna=False):
        grp = grp.sort_values("entry_datetime")
        max_active_layers, peak_notional = _active_stats(grp)
        parts.append(
            {
                "pos_id": str(pos_id),
                "symbol": str(grp.get("symbol", pd.Series([""])).iloc[0]),
                "exchange": str(grp.get("exchange", pd.Series([""])).iloc[0]),
                "direction": str(grp.get("side", pd.Series([""])).iloc[0]),
                "first_entry": pd.to_datetime(grp["entry_datetime"], errors="coerce").min(),
                "last_exit": pd.to_datetime(grp["exit_datetime"], errors="coerce").max(),
                "layer_count": int(pd.to_numeric(grp["layer_id"], errors="coerce").nunique(dropna=True)),
                "max_active_layers": int(max_active_layers),
                "peak_notional": float(peak_notional),
            }
        )
    return pd.DataFrame(parts)


def _evaluate_oot_real_execution(
    prediction_df: pd.DataFrame,
    *,
    cfg: OotEvaluationConfig = DEFAULT_OOT_EVAL_CONFIG,
    intrabar_bar_provider: Callable[[str, str, pd.Timestamp, pd.Timestamp, str], pd.DataFrame] | None = None,
    extra_outputs: dict[str, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate OOT performance on real executed trades with model gating."""
    monthly_cols = [
        "month",
        "trade_count",
        "win_count",
        "loss_count",
        "win_rate",
        "gross_pnl",
        "net_pnl",
        "month_start_equity",
        "month_end_equity",
        "monthly_return_pct",
        "monthly_excess_return_pct",
        "cum_return_pct",
    ]
    summary_cols = [
        "oot_rows",
        "executed_rows",
        "trade_count",
        "selected_rows",
        "blocked_rows",
        "blocked_margin_cash_rows",
        "blocked_leverage_rows",
        "blocked_limit_move_rows",
        "blocked_daily_position_rows",
        "blocked_weekly_drawdown_rows",
        "blocked_weekly_budget_rows",
        "blocked_monthly_drawdown_rows",
        "blocked_symbol_cap_rows",
        "blocked_symbol_concurrent_rows",
        "blocked_total_concurrent_rows",
        "blocked_htf_rows",
        "blocked_ranker_rows",
        "blocked_throttle_rows",
        "blocked_pyramid_rows",
        "stop_loss_exit_rows",
        "trailing_stop_exit_rows",
        "horizon_exit_rows",
        "monthly_obs",
        "gross_pnl",
        "net_pnl",
        "roll_cost_total",
        "total_return_pct",
        "avg_monthly_return_pct",
        "std_monthly_return_pct",
        "monthly_excess_return_pct",
        "std_monthly_excess_return_pct",
        "monthly_sharpe",
        "max_drawdown_pct",
        "annualized_return_pct",
        "calmar_like",
    ]
    trade_cols = [
        "datetime",
        "entry_datetime",
        "signal_datetime",
        "exit_datetime",
        "symbol",
        "exchange",
        "interval",
        "signal_type",
        "side",
        "entry_action",
        "exit_action",
        "execution_status",
        "block_reason",
        "entry_fill_datetime",
        "entry_fill_price",
        "planned_exit_datetime",
        "planned_exit_price",
        "stop_loss_price",
        "stop_hit_datetime",
        "stop_hit_price",
        "stop_triggered",
        "final_exit_datetime",
        "final_exit_price",
        "stop_tracking_interval",
        "exit_reason",
        "trailing_activated",
        "trailing_stop_price",
        "pos_id",
        "layer_id",
        "layer_interval",
        "htf_alignment",
        "throttle_level_at_entry",
        "ranker_score",
        "ranker_score_threshold",
        "model_signal_type",
        "window_id",
        "pred_split",
        "entry_price",
        "exit_price_ref",
        "trigger",
        "stop_price",
        "trade_filter_prob",
        "pred_regime_label",
        "pred_mfe_atr",
        "pred_mae_atr",
        "final_decision_score",
        "final_decision_model_kind",
        "future_mfe_atr",
        "future_mae_atr",
        "future_pnl_atr",
        "realized_return_atr",
        "position_scale",
        "position_notional",
        "entry_amount",
        "exit_amount",
        "position_qty",
        "max_loss_amount",
        "expected_loss_pct",
        "expected_loss_amount",
        "available_cash_before_entry",
        "margin_used_before_entry",
        "open_notional_before_entry",
        "open_notional_at_entry",
        "weekly_drawdown_pct_before_entry",
        "gross_return_pct",
        "trade_return_pct",
        "net_return_pct",
        "gross_pnl",
        "cost_pct",
        "net_pnl",
        "roll_cost",
        "pnl_amount",
        "equity_before",
        "equity_after",
        "month",
    ]
    throttle_cols = [
        "timestamp",
        "equity",
        "drawdown_pct",
        "weekly_return_pct",
        "monthly_return_pct",
        "level",
        "score_threshold",
    ]

    def _init_empty_extra_outputs() -> None:
        if extra_outputs is None:
            return
        extra_outputs["throttle_log"] = pd.DataFrame(columns=throttle_cols)
        extra_outputs["position_lifetime"] = _build_position_lifetime_table(pd.DataFrame(columns=trade_cols))

    if prediction_df.empty:
        _init_empty_extra_outputs()
        return (
            pd.DataFrame(columns=monthly_cols),
            pd.DataFrame(columns=summary_cols),
            pd.DataFrame(columns=trade_cols),
        )

    df = prediction_df.copy()
    # M2：在 OOT 入口剔除 symbol_disable_manifest 标记的品种。
    # 训练阶段已经过滤过，但 prediction_df 可能来自历史模型 / 第三方流程，
    # 这里再做一次防御性过滤，保证评估口径与训练口径一致。
    from cta.config.symbol_disable import mask_disabled_rows

    df = mask_disabled_rows(df, symbol_column="symbol")
    if cfg.use_test_split_only and "pred_split" in df.columns:
        df = df.loc[df["pred_split"].astype(str).str.lower() == "test"].copy()
    if df.empty:
        _init_empty_extra_outputs()
        return (
            pd.DataFrame(columns=monthly_cols),
            pd.DataFrame(columns=summary_cols),
            pd.DataFrame(columns=trade_cols),
        )

    if cfg.use_last_window_only and "window_id" in df.columns:
        w = pd.to_numeric(df["window_id"], errors="coerce")
        if w.notna().any():
            df = df.loc[w == w.max()].copy()
    if df.empty:
        _init_empty_extra_outputs()
        return (
            pd.DataFrame(columns=monthly_cols),
            pd.DataFrame(columns=summary_cols),
            pd.DataFrame(columns=trade_cols),
        )

    oot_rows = int(len(df))
    # portfolio_logic 的 HTF/校准参考源：使用 OOT 过滤后的全体候选（包含非 executed）
    # 这样 day/60min 状态行可以给执行样本（executed=1）提供方向闸上下文。
    htf_reference_df = df.copy()

    exec_mask = pd.to_numeric(df.get("is_executed", 0), errors="coerce").fillna(0).astype(int) == 1
    if cfg.require_executed_only:
        df = df.loc[exec_mask].copy()
    executed_rows = int(exec_mask.sum()) if cfg.require_executed_only else int(len(df))
    if df.empty:
        summary = pd.DataFrame(
            [
                {
                    "oot_rows": oot_rows,
                    "executed_rows": executed_rows,
                    "trade_count": 0,
                    "selected_rows": 0,
                    "blocked_rows": 0,
                    "blocked_margin_cash_rows": 0,
                    "blocked_leverage_rows": 0,
                    "blocked_limit_move_rows": 0,
                    "blocked_daily_position_rows": 0,
                    "blocked_weekly_drawdown_rows": 0,
                    "blocked_weekly_budget_rows": 0,
                    "blocked_monthly_drawdown_rows": 0,
                    "blocked_symbol_cap_rows": 0,
                    "blocked_symbol_concurrent_rows": 0,
                    "blocked_total_concurrent_rows": 0,
                    "blocked_htf_rows": 0,
                    "blocked_ranker_rows": 0,
                    "blocked_throttle_rows": 0,
                    "blocked_pyramid_rows": 0,
                    "stop_loss_exit_rows": 0,
                    "trailing_stop_exit_rows": 0,
                    "horizon_exit_rows": 0,
                    "monthly_obs": 0,
                    "gross_pnl": 0.0,
                    "net_pnl": 0.0,
                    "roll_cost_total": 0.0,
                    "total_return_pct": 0.0,
                    "avg_monthly_return_pct": float("nan"),
                    "std_monthly_return_pct": float("nan"),
                    "monthly_excess_return_pct": float("nan"),
                    "std_monthly_excess_return_pct": float("nan"),
                    "monthly_sharpe": float("nan"),
                    "max_drawdown_pct": float("nan"),
                    "annualized_return_pct": float("nan"),
                    "calmar_like": float("nan"),
                }
            ]
        )
        return pd.DataFrame(columns=monthly_cols), summary, pd.DataFrame(columns=trade_cols)

    gate = pd.Series(True, index=df.index, dtype=bool)
    gate_by_legacy = pd.Series(True, index=df.index, dtype=bool)
    if cfg.use_trade_filter_gate and "trade_filter_prob" in df.columns:
        prob = pd.to_numeric(df["trade_filter_prob"], errors="coerce").fillna(0.0)
        gate_by_legacy = gate_by_legacy & (prob >= float(cfg.trade_filter_threshold))

    if cfg.use_regime_gate and "pred_regime_label" in df.columns:
        regime = df["pred_regime_label"].astype(str).str.lower()
        side = df.get("side", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower()
        pass_regime = pd.Series(True, index=df.index, dtype=bool)
        pass_regime.loc[side == "long"] = regime.loc[side == "long"] != "trend_down"
        pass_regime.loc[side == "short"] = regime.loc[side == "short"] != "trend_up"
        if not bool(cfg.allow_range_in_regime_gate):
            pass_regime = pass_regime & (regime != "range")
        gate_by_legacy = gate_by_legacy & pass_regime

    if cfg.use_mfe_mae_gate and {"pred_mfe_atr", "pred_mae_atr"}.issubset(set(df.columns)):
        pred_mfe = pd.to_numeric(df["pred_mfe_atr"], errors="coerce").fillna(0.0)
        pred_mae = pd.to_numeric(df["pred_mae_atr"], errors="coerce").fillna(0.0)
        pred_edge = pred_mfe - float(cfg.mae_penalty) * pred_mae
        gate_by_legacy = gate_by_legacy & (pred_edge >= float(cfg.min_pred_edge_atr))

    gate_by_stacking = pd.Series(True, index=df.index, dtype=bool)
    stacking_col = str(getattr(cfg, "stacking_score_column", "final_decision_score"))
    has_stacking_col = stacking_col in df.columns
    if bool(getattr(cfg, "use_stacking_gate", False)) and has_stacking_col:
        stack_score = pd.to_numeric(df[stacking_col], errors="coerce").fillna(0.0)
        gate_by_stacking = stack_score >= float(getattr(cfg, "stacking_score_threshold", 0.5))
        if bool(getattr(cfg, "stacking_gate_overrides_individual_gates", True)):
            gate = gate_by_stacking
        else:
            gate = gate_by_legacy & gate_by_stacking
    else:
        gate = gate_by_legacy

    selected = df.loc[gate].copy()
    selected_rows = int(len(selected))
    if selected.empty:
        summary = pd.DataFrame(
            [
                {
                    "oot_rows": oot_rows,
                    "executed_rows": executed_rows,
                    "trade_count": selected_rows,
                    "selected_rows": selected_rows,
                    "blocked_rows": 0,
                    "blocked_margin_cash_rows": 0,
                    "blocked_leverage_rows": 0,
                    "blocked_limit_move_rows": 0,
                    "blocked_daily_position_rows": 0,
                    "blocked_weekly_drawdown_rows": 0,
                    "blocked_weekly_budget_rows": 0,
                    "blocked_monthly_drawdown_rows": 0,
                    "blocked_symbol_cap_rows": 0,
                    "blocked_symbol_concurrent_rows": 0,
                    "blocked_total_concurrent_rows": 0,
                    "blocked_htf_rows": 0,
                    "blocked_ranker_rows": 0,
                    "blocked_throttle_rows": 0,
                    "blocked_pyramid_rows": 0,
                    "stop_loss_exit_rows": 0,
                    "trailing_stop_exit_rows": 0,
                    "horizon_exit_rows": 0,
                    "monthly_obs": 0,
                    "gross_pnl": 0.0,
                    "net_pnl": 0.0,
                    "roll_cost_total": 0.0,
                    "total_return_pct": 0.0,
                    "avg_monthly_return_pct": float("nan"),
                    "std_monthly_return_pct": float("nan"),
                    "monthly_excess_return_pct": float("nan"),
                    "std_monthly_excess_return_pct": float("nan"),
                    "monthly_sharpe": float("nan"),
                    "max_drawdown_pct": float("nan"),
                    "annualized_return_pct": float("nan"),
                    "calmar_like": float("nan"),
                }
            ]
        )
        return pd.DataFrame(columns=monthly_cols), summary, pd.DataFrame(columns=trade_cols)

    selected["datetime"] = pd.to_datetime(selected["datetime"], errors="coerce")
    if "signal_datetime" in selected.columns:
        selected["signal_datetime"] = pd.to_datetime(selected["signal_datetime"], errors="coerce")
    if "entry_datetime" in selected.columns:
        selected["entry_datetime"] = pd.to_datetime(selected["entry_datetime"], errors="coerce")
    else:
        selected["entry_datetime"] = selected["datetime"]
    if "exit_datetime" in selected.columns:
        selected["exit_datetime"] = pd.to_datetime(selected["exit_datetime"], errors="coerce")
    else:
        selected["exit_datetime"] = pd.NaT
    exit_missing_mask = selected["exit_datetime"].isna()
    selected["entry_datetime"] = selected["entry_datetime"].fillna(selected["datetime"])
    selected["exit_datetime"] = selected["exit_datetime"].fillna(selected["entry_datetime"])
    if exit_missing_mask.any():
        selected.loc[exit_missing_mask, "exit_datetime"] = (
            selected.loc[exit_missing_mask, "entry_datetime"] + pd.to_timedelta(1, unit="ns")
        )
    non_increasing_exit = selected["exit_datetime"] <= selected["entry_datetime"]
    if non_increasing_exit.any():
        selected.loc[non_increasing_exit, "exit_datetime"] = (
            selected.loc[non_increasing_exit, "entry_datetime"] + pd.to_timedelta(1, unit="ns")
        )
    selected = selected.dropna(subset=["datetime", "entry_datetime", "exit_datetime"]).copy()
    if selected.empty:
        _init_empty_extra_outputs()
        return (
            pd.DataFrame(columns=monthly_cols),
            pd.DataFrame(columns=summary_cols),
            pd.DataFrame(columns=trade_cols),
        )

    realized_atr = (
        pd.to_numeric(selected.get("future_mfe_atr", 0.0), errors="coerce").fillna(0.0)
        - float(cfg.mae_penalty) * pd.to_numeric(selected.get("future_mae_atr", 0.0), errors="coerce").fillna(0.0)
    )
    selected["realized_return_atr"] = realized_atr
    gross_ret_pct = realized_atr * float(cfg.risk_per_trade_pct)
    # P0 fix：trade_return 下限应该用"实际止损率"（intrabar_stop_loss_pct），
    # 不是"单笔最大亏损占权益的比例"（max_single_loss_pct）。这两个字段口径完全不同：
    #   - intrabar_stop_loss_pct：止损价距入场价的 %，是 trade 层面的 return floor
    #   - max_single_loss_pct：单笔亏损金额占权益的 %，是 sizing 层面的总风险预算
    # 旧实现把后者错当前者用，导致所有 trade_return 被截到 -0.1% 看不到真实分布。
    trade_ret_pct = np.maximum(gross_ret_pct, -float(cfg.intrabar_stop_loss_pct))
    cost_pct = float(cfg.commission_pct_per_trade) + float(cfg.slippage_pct_per_trade)
    net_ret_pct = trade_ret_pct - cost_pct
    selected["gross_return_pct"] = pd.to_numeric(gross_ret_pct, errors="coerce").fillna(0.0)
    selected["trade_return_pct"] = pd.to_numeric(net_ret_pct, errors="coerce").fillna(0.0)
    selected["net_return_pct"] = selected["trade_return_pct"]
    selected["cost_pct"] = cost_pct
    selected["month"] = selected["exit_datetime"].dt.to_period("M").dt.to_timestamp()
    selected = selected.sort_values(["entry_datetime", "exit_datetime"]).reset_index(drop=True)

    n = len(selected)
    selected["execution_status"] = "pending"
    selected["block_reason"] = ""
    selected["equity_before"] = np.nan
    selected["equity_after"] = np.nan
    selected["gross_pnl"] = 0.0
    selected["net_pnl"] = 0.0
    selected["roll_cost"] = 0.0
    selected["pnl_amount"] = 0.0
    selected["max_loss_amount"] = np.nan
    selected["position_scale"] = 0.0
    selected["position_notional"] = 0.0
    selected["entry_amount"] = 0.0
    selected["exit_amount"] = 0.0
    selected["position_qty"] = np.nan
    selected["expected_loss_pct"] = np.nan
    selected["expected_loss_amount"] = np.nan
    selected["available_cash_before_entry"] = np.nan
    selected["margin_used_before_entry"] = np.nan
    selected["open_notional_before_entry"] = np.nan
    selected["open_notional_at_entry"] = np.nan
    selected["weekly_drawdown_pct_before_entry"] = np.nan
    selected["entry_fill_datetime"] = pd.NaT
    selected["entry_fill_price"] = np.nan
    selected["planned_exit_datetime"] = selected["exit_datetime"]
    selected["planned_exit_price"] = np.nan
    selected["stop_loss_price"] = np.nan
    selected["stop_hit_datetime"] = pd.NaT
    selected["stop_hit_price"] = np.nan
    selected["stop_triggered"] = 0
    selected["final_exit_datetime"] = selected["exit_datetime"]
    selected["final_exit_price"] = np.nan
    selected["stop_tracking_interval"] = ""
    selected["exit_reason"] = ""
    selected["trailing_activated"] = 0
    selected["trailing_stop_price"] = np.nan
    selected["pos_id"] = ""
    selected["layer_id"] = np.nan
    selected["layer_interval"] = ""
    selected["htf_alignment"] = ""
    selected["throttle_level_at_entry"] = ""
    selected["ranker_score"] = np.nan
    selected["ranker_score_threshold"] = np.nan

    if "pred_mae_atr" in selected.columns:
        pred_mae_series = pd.to_numeric(selected["pred_mae_atr"], errors="coerce")
    else:
        pred_mae_series = pd.Series(np.nan, index=selected.index, dtype=float)
    pred_mae_arr = pred_mae_series.to_numpy()
    if "entry_price" in selected.columns:
        entry_price_series = pd.to_numeric(selected["entry_price"], errors="coerce")
    else:
        entry_price_series = pd.Series(np.nan, index=selected.index, dtype=float)
    entry_price_arr = entry_price_series.to_numpy()
    gross_pct_arr = np.array(
        pd.to_numeric(selected["gross_return_pct"], errors="coerce").fillna(0.0),
        dtype=float,
        copy=True,
    )
    net_pct_arr = np.array(
        pd.to_numeric(selected["trade_return_pct"], errors="coerce").fillna(0.0),
        dtype=float,
        copy=True,
    )
    side_arr = selected.get("side", pd.Series([""] * n, index=selected.index)).astype(str).str.lower().to_numpy()
    symbol_arr = selected.get("symbol", pd.Series([""] * n, index=selected.index)).astype(str).str.upper().to_numpy()
    exchange_arr = selected.get("exchange", pd.Series([""] * n, index=selected.index)).astype(str).str.upper().to_numpy()
    if "feature_is_limit_up_close" in selected.columns:
        limit_up_series = pd.to_numeric(selected["feature_is_limit_up_close"], errors="coerce").fillna(0.0)
    else:
        limit_up_series = pd.Series(0.0, index=selected.index, dtype=float)
    if "feature_is_limit_down_close" in selected.columns:
        limit_down_series = pd.to_numeric(selected["feature_is_limit_down_close"], errors="coerce").fillna(0.0)
    else:
        limit_down_series = pd.Series(0.0, index=selected.index, dtype=float)
    limit_up_arr = limit_up_series.to_numpy(dtype=float)
    limit_down_arr = limit_down_series.to_numpy(dtype=float)
    if "atr_pct_at_entry" in selected.columns:
        atr_pct_arr = pd.to_numeric(selected["atr_pct_at_entry"], errors="coerce").to_numpy(dtype=float)
    elif "feature_atr14" in selected.columns:
        atr_raw = pd.to_numeric(selected["feature_atr14"], errors="coerce").to_numpy(dtype=float)
        atr_pct_arr = np.divide(
            atr_raw,
            np.where(np.isfinite(entry_price_arr) & (entry_price_arr > 0), entry_price_arr, np.nan),
        )
    else:
        atr_pct_arr = np.full(n, np.nan, dtype=float)

    pl_cfg = getattr(cfg, "portfolio_logic", None)
    use_pl_runtime = bool(getattr(cfg, "use_portfolio_logic_runtime", False))
    use_pl_trailing = bool(
        use_pl_runtime
        and pl_cfg is not None
        and bool(getattr(pl_cfg, "enable_trailing", False))
    )
    use_pl_htf = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, "enable_htf_gate", False)))
    use_pl_ranker = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, "enable_ranker", False)))
    use_pl_throttle = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, "enable_risk_throttle", False)))
    use_pl_pyramid = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, "enable_pyramid", False)))

    htf_gate: HtfGate | None = None
    htf_ref_by_interval: dict[str, pd.DataFrame] = {}
    htf_state_cache: dict[pd.Timestamp, dict[tuple[str, str], dict[str, Any]]] = {}
    if use_pl_htf:
        htf_gate = HtfGate(pl_cfg.interval_gate)
        ref = htf_reference_df.copy()
        if "datetime" in ref.columns:
            ref["datetime"] = pd.to_datetime(ref["datetime"], errors="coerce")
        for itv in pl_cfg.interval_gate.htf_intervals:
            ref_interval = ref.get("interval", pd.Series([""] * len(ref), index=ref.index))
            ref_interval_norm = ref_interval.map(normalize_portfolio_interval)
            part = ref.loc[ref_interval_norm == normalize_portfolio_interval(itv)].copy()
            if not part.empty:
                htf_ref_by_interval[normalize_portfolio_interval(itv)] = part

    ranker: OpportunityRanker | None = None
    if use_pl_ranker:
        ranker = OpportunityRanker(pl_cfg.ranker)

    risk_throttle: RiskThrottle | None = None
    equity_tracker: EquityTracker | None = None
    throttle_level = None
    if use_pl_throttle:
        risk_throttle = RiskThrottle(pl_cfg.risk_throttle)
        equity_tracker = EquityTracker()

    pyramid_manager: PyramidManager | None = None
    active_pyramid_by_sym_dir: dict[tuple[str, str, str], PyramidPosition] = {}
    idx_layer_ref: dict[int, tuple[PyramidPosition, int]] = {}
    if use_pl_pyramid:
        pyramid_manager = PyramidManager(pl_cfg.pyramid)

    use_intrabar = bool(getattr(cfg, "use_intrabar_stop_tracking", False))
    if use_intrabar:
        preferred_intrabar_raw = str(getattr(cfg, "intrabar_tracking_interval", "60min"))
        preferred_intrabar = normalize_interval(preferred_intrabar_raw)
        fallback_raw = tuple(getattr(cfg, "intrabar_tracking_fallback_intervals", ()))
        interval_candidates: list[str] = []
        for raw in (preferred_intrabar, *fallback_raw):
            try:
                norm = normalize_interval(str(raw))
            except Exception:
                continue
            if norm not in interval_candidates:
                interval_candidates.append(norm)
        if not interval_candidates:
            interval_candidates = ["60min"]

        # 预加载 intrabar 数据并做逐笔止损/退出路径模拟。
        span_by_symbol: dict[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]] = {}
        for key, grp in selected.groupby(
            [
                selected.get("symbol", pd.Series([""] * n, index=selected.index)).astype(str).str.upper(),
                selected.get("exchange", pd.Series([""] * n, index=selected.index)).astype(str).str.upper(),
            ]
        ):
            entry_min = pd.to_datetime(grp["entry_datetime"], errors="coerce").min()
            exit_max = pd.to_datetime(grp["exit_datetime"], errors="coerce").max()
            if pd.notna(entry_min) and pd.notna(exit_max):
                span_by_symbol[(str(key[0]).upper(), str(key[1]).upper())] = (pd.Timestamp(entry_min), pd.Timestamp(exit_max))
        bar_cache_by_interval = {
            itv: _IntrabarBarCache(interval=itv, date_span_by_symbol=span_by_symbol)
            for itv in interval_candidates
        }
        intrabar_stop_pct = float(getattr(cfg, "intrabar_stop_loss_pct", float(cfg.max_single_loss_pct)))

        for idx in range(n):
            ent = pd.to_datetime(selected.iloc[idx]["entry_datetime"], errors="coerce")
            exi = pd.to_datetime(selected.iloc[idx]["exit_datetime"], errors="coerce")
            if pd.isna(ent) or pd.isna(exi):
                continue
            sym = str(symbol_arr[idx]).upper()
            ex = str(exchange_arr[idx]).upper()
            hint = float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else float("nan")
            used_interval = interval_candidates[0]
            if intrabar_bar_provider is not None:
                used_interval = preferred_intrabar_raw
                try:
                    bars = intrabar_bar_provider(
                        sym,
                        ex,
                        pd.Timestamp(ent),
                        pd.Timestamp(exi),
                        used_interval,
                    )
                except Exception:
                    bars = pd.DataFrame()
            else:
                bars = pd.DataFrame()
                for itv in interval_candidates:
                    bars_try = bar_cache_by_interval[itv].slice(sym, ex, pd.Timestamp(ent), pd.Timestamp(exi))
                    if not bars_try.empty:
                        bars = bars_try
                        used_interval = itv
                        break

            if use_pl_trailing:
                row_interval = normalize_portfolio_interval(selected.iloc[idx].get("interval", ""))
                row_regime = str(selected.iloc[idx].get("pred_regime_label", "")).strip().lower()
                atr_pct = float(atr_pct_arr[idx]) if idx < len(atr_pct_arr) and np.isfinite(atr_pct_arr[idx]) else None
                sim = simulate_trailing_exit(
                    side=str(side_arr[idx]),
                    entry_ts=pd.Timestamp(ent),
                    planned_exit_ts=pd.Timestamp(exi),
                    entry_price_hint=hint,
                    stop_loss_pct=intrabar_stop_pct,
                    bars=bars,
                    interval=row_interval,
                    atr_pct_at_entry=atr_pct,
                    regime_label=row_regime,
                    cfg=pl_cfg.trailing,
                    horizon_cfg=pl_cfg.horizon_extend if bool(getattr(pl_cfg, "enable_horizon_extend", False)) else None,
                )
            else:
                sim = _simulate_intrabar_exit(
                    side=str(side_arr[idx]),
                    entry_ts=pd.Timestamp(ent),
                    planned_exit_ts=pd.Timestamp(exi),
                    entry_price_hint=hint,
                    stop_loss_pct=intrabar_stop_pct,
                    bars=bars,
                )
            selected.at[idx, "entry_fill_datetime"] = sim["entry_fill_datetime"]
            selected.at[idx, "entry_fill_price"] = sim["entry_fill_price"]
            selected.at[idx, "planned_exit_datetime"] = sim["planned_exit_datetime"]
            selected.at[idx, "planned_exit_price"] = sim["planned_exit_price"]
            selected.at[idx, "stop_loss_price"] = sim["stop_loss_price"]
            selected.at[idx, "stop_hit_datetime"] = sim["stop_hit_datetime"]
            selected.at[idx, "stop_hit_price"] = sim["stop_hit_price"]
            selected.at[idx, "stop_triggered"] = int(sim["stop_triggered"])
            selected.at[idx, "final_exit_datetime"] = sim["final_exit_datetime"]
            selected.at[idx, "final_exit_price"] = sim["final_exit_price"]
            selected.at[idx, "stop_tracking_interval"] = used_interval
            selected.at[idx, "exit_reason"] = sim["exit_reason"]
            selected.at[idx, "trailing_activated"] = int(sim.get("trailing_activated", 0))
            selected.at[idx, "trailing_stop_price"] = sim.get("trailing_stop_price", np.nan)
            ret = float(sim["price_return_pct"]) if np.isfinite(sim["price_return_pct"]) else float("nan")
            if np.isfinite(ret):
                # P0 fix: 同上，用 intrabar_stop_loss_pct 而非 max_single_loss_pct 作 return floor
                g = max(ret, -float(cfg.intrabar_stop_loss_pct))
                gross_pct_arr[idx] = float(g)
                net_pct_arr[idx] = float(g - cost_pct)

        selected["gross_return_pct"] = pd.Series(gross_pct_arr, index=selected.index, dtype=float)
        selected["trade_return_pct"] = pd.Series(net_pct_arr, index=selected.index, dtype=float)
        selected["net_return_pct"] = selected["trade_return_pct"]
        # 资金仿真和汇总按最终退出时刻对齐
        final_exit_dt = pd.to_datetime(selected["final_exit_datetime"], errors="coerce")
        valid_final = final_exit_dt.notna()
        selected.loc[valid_final, "exit_datetime"] = final_exit_dt.loc[valid_final]
    else:
        selected["entry_fill_datetime"] = selected["entry_datetime"]
        selected["entry_fill_price"] = pd.to_numeric(selected.get("entry_price", np.nan), errors="coerce")
        selected["planned_exit_datetime"] = selected["exit_datetime"]
        selected["final_exit_datetime"] = selected["exit_datetime"]
        selected["planned_exit_price"] = pd.to_numeric(selected.get("exit_price_ref", np.nan), errors="coerce")
        selected["final_exit_price"] = pd.to_numeric(selected.get("exit_price_ref", np.nan), errors="coerce")
        stop_pct = float(getattr(cfg, "intrabar_stop_loss_pct", float(cfg.max_single_loss_pct)))
        side_s = selected.get("side", pd.Series([""] * len(selected), index=selected.index)).astype(str).str.lower()
        entry_fill_s = pd.to_numeric(selected.get("entry_fill_price", np.nan), errors="coerce")
        selected["stop_loss_price"] = np.where(
            side_s == "short",
            entry_fill_s * (1.0 + stop_pct),
            entry_fill_s * (1.0 - stop_pct),
        )
        selected["stop_tracking_interval"] = "atr_proxy"
        selected["exit_reason"] = "atr_proxy"

    entry_fill_arr = pd.to_numeric(selected.get("entry_fill_price", np.nan), errors="coerce").to_numpy(dtype=float)
    stop_loss_arr = pd.to_numeric(selected.get("stop_loss_price", np.nan), errors="coerce").to_numpy(dtype=float)

    entry_action_arr: list[str] = []
    exit_action_arr: list[str] = []
    for side in side_arr:
        if side == "long":
            entry_action_arr.append("buy")
            exit_action_arr.append("sell")
        elif side == "short":
            entry_action_arr.append("short")
            exit_action_arr.append("cover")
        else:
            entry_action_arr.append("open")
            exit_action_arr.append("close")
    selected["entry_action"] = entry_action_arr
    selected["exit_action"] = exit_action_arr

    def _week_start(ts: pd.Timestamp) -> pd.Timestamp:
        return (ts.normalize() - pd.Timedelta(days=int(ts.weekday()))).normalize()

    cash = float(cfg.initial_capital)
    margin_used = 0.0
    open_notional = 0.0
    open_positions: dict[int, dict[str, Any]] = {}
    # per-symbol 累计未平仓名义金额 / 在仓笔数（用于 single-symbol cap 与并发笔数约束）
    symbol_open_notional: dict[str, float] = {}
    symbol_open_count: dict[str, int] = {}
    cluster_open_notional: dict[str, float] = {}
    cluster_open_count: dict[str, int] = {}
    # 若样本里有 symbol 列缺失 / 空串，per-symbol cap 对这些笔会失效（fallback 到组合 cap）。
    # 这里一次性预警，避免 hot loop 里 per-idx 刷屏；预警后流程继续，便于诊断。
    missing_symbol_count = int(
        pd.Series(symbol_arr).astype(str).str.strip().eq("").sum()
        + pd.Series(symbol_arr).isna().sum()
    )
    if missing_symbol_count > 0:
        logger.warning(
            "OOT evaluator: %d/%d rows missing symbol; per-symbol cap will be skipped "
            "for these rows (organization cap still applies). check upstream feature pipeline.",
            missing_symbol_count,
            int(len(symbol_arr)),
        )
    day_key: pd.Timestamp | None = None
    day_start_equity = float(cfg.initial_capital)
    day_new_notional = 0.0
    week_key: pd.Timestamp | None = None
    week_peak_equity = float(cfg.initial_capital)
    week_dd_breached = False
    month_key: pd.Timestamp | None = None
    month_peak_equity = float(cfg.initial_capital)
    month_dd_breached = False
    throttle_log_rows: list[dict[str, Any]] = []

    entries_map: dict[pd.Timestamp, list[int]] = {}
    exits_map: dict[pd.Timestamp, list[int]] = {}
    for idx in range(n):
        ent = pd.to_datetime(selected.iloc[idx]["entry_datetime"], errors="coerce")
        exi = pd.to_datetime(selected.iloc[idx]["exit_datetime"], errors="coerce")
        if pd.isna(ent) or pd.isna(exi) or exi < ent:
            selected.at[idx, "execution_status"] = "blocked_invalid_time"
            selected.at[idx, "block_reason"] = "invalid_time"
            continue
        # 若同一时间戳开平仓（例如入场 bar 内立即触发止损），
        # 资金仿真里用 +1ns 的事件时序保证“先开后平”，避免仓位被锁死到回测末尾。
        exi_event = exi if exi > ent else (ent + pd.Timedelta(1, unit="ns"))
        entries_map.setdefault(ent, []).append(idx)
        exits_map.setdefault(exi_event, []).append(idx)
    timeline = sorted(set(entries_map.keys()) | set(exits_map.keys()))

    for ts in timeline:
        cur_day = ts.normalize()
        if day_key is None or cur_day != day_key:
            day_key = cur_day
            day_start_equity = float(cash)
            day_new_notional = 0.0

        cur_week = _week_start(ts)
        if week_key is None or cur_week != week_key:
            week_key = cur_week
            week_peak_equity = float(cash)
            week_dd_breached = False

        cur_month = ts.to_period("M").to_timestamp()
        if month_key is None or cur_month != month_key:
            month_key = cur_month
            month_peak_equity = float(cash)
            month_dd_breached = False

        # exits first: release margin and realize PnL
        for idx in exits_map.get(ts, []):
            if idx not in open_positions:
                continue
            pos = open_positions.pop(idx)
            margin_used = max(0.0, float(margin_used - float(pos["margin"])))
            open_notional = max(0.0, float(open_notional - float(pos["notional"])))
            pos_sym = str(pos.get("symbol", ""))
            if pos_sym:
                symbol_open_notional[pos_sym] = max(
                    0.0, float(symbol_open_notional.get(pos_sym, 0.0) - float(pos["notional"]))
                )
                symbol_open_count[pos_sym] = max(0, int(symbol_open_count.get(pos_sym, 0) - 1))
            pos_cluster = str(pos.get("cluster", "other"))
            cluster_open_notional[pos_cluster] = max(
                0.0,
                float(cluster_open_notional.get(pos_cluster, 0.0) - float(pos["notional"])),
            )
            cluster_open_count[pos_cluster] = max(0, int(cluster_open_count.get(pos_cluster, 0) - 1))
            if use_pl_pyramid and idx in idx_layer_ref:
                pos_ref, layer_id_ref = idx_layer_ref.pop(idx)
                for layer in pos_ref.layers:
                    if int(layer.layer_id) == int(layer_id_ref):
                        layer.exited = True
                        break
                sym_dir = (pos_ref.symbol, pos_ref.exchange, pos_ref.direction)
                if len(pos_ref.active_layers) == 0:
                    active_pyramid_by_sym_dir.pop(sym_dir, None)
            gross_pnl = float(pos["notional"]) * float(pos["gross_ret_pct"])
            roll_cost = _calc_roll_cost(
                symbol=pos_sym,
                notional=float(pos["notional"]),
                entry_ts=pd.Timestamp(pos.get("entry_ts", ts)),
                exit_ts=pd.Timestamp(ts),
                cfg=cfg,
            )
            net_pnl = float(pos["notional"]) * float(pos["net_ret_pct"]) - float(roll_cost)
            cash = float(cash + net_pnl)

            selected.at[idx, "equity_before"] = float(pos["entry_equity"])
            selected.at[idx, "equity_after"] = float(cash)
            selected.at[idx, "gross_pnl"] = gross_pnl
            selected.at[idx, "net_pnl"] = net_pnl
            selected.at[idx, "roll_cost"] = float(roll_cost)
            selected.at[idx, "pnl_amount"] = net_pnl
            selected.at[idx, "entry_amount"] = float(pos["notional"])
            selected.at[idx, "exit_amount"] = float(pos["notional"] + net_pnl)
            selected.at[idx, "execution_status"] = "executed"

            week_peak_equity = max(float(week_peak_equity), float(cash))
            month_peak_equity = max(float(month_peak_equity), float(cash))
            if week_peak_equity > 0:
                dd_now = float(cash / week_peak_equity - 1.0)
            else:
                dd_now = float("nan")
            if np.isfinite(dd_now) and dd_now <= -float(cfg.weekly_max_drawdown_pct):
                week_dd_breached = True
            if month_peak_equity > 0:
                mdd_now = float(cash / month_peak_equity - 1.0)
            else:
                mdd_now = float("nan")
            if np.isfinite(mdd_now) and mdd_now <= -float(cfg.monthly_max_drawdown_pct):
                month_dd_breached = True

        pending_entries = [idx for idx in entries_map.get(ts, []) if str(selected.at[idx, "execution_status"]) == "pending"]
        entry_order = list(pending_entries)
        score_threshold_for_bar = float("nan")
        throttle_level_name = ""

        if use_pl_throttle and risk_throttle is not None and equity_tracker is not None:
            equity_tracker.on_bar(float(cash), pd.Timestamp(ts))
            snap = equity_tracker.snapshot()
            throttle_level = risk_throttle.compute(snap, throttle_level)
            throttle_level_name = str(throttle_level.name)
            score_threshold_for_bar = float(throttle_level.score_pctl_threshold) / 100.0
            throttle_log_rows.append(
                {
                    "timestamp": pd.Timestamp(ts),
                    "equity": float(cash),
                    "drawdown_pct": float(snap.drawdown_pct),
                    "weekly_return_pct": float(snap.weekly_return_pct),
                    "monthly_return_pct": float(snap.monthly_return_pct),
                    "level": throttle_level_name,
                    "score_threshold": float(score_threshold_for_bar),
                }
            )
            if str(throttle_level.name) == "halt":
                for idx in pending_entries:
                    selected.at[idx, "execution_status"] = "blocked_throttle_halt"
                    selected.at[idx, "block_reason"] = "blocked_throttle_halt"
                    selected.at[idx, "equity_before"] = float(cash)
                    selected.at[idx, "equity_after"] = float(cash)
                    selected.at[idx, "throttle_level_at_entry"] = throttle_level_name
                entry_order = []

        if use_pl_htf and htf_gate is not None and entry_order:
            if ts not in htf_state_cache:
                htf_state_cache[ts] = htf_gate.compute_htf_state(htf_ref_by_interval, as_of=pd.Timestamp(ts))
            htf_state_now = htf_state_cache.get(ts, {})
            gate_rows = []
            for idx in entry_order:
                gate_rows.append(
                    {
                        "_row_idx": int(idx),
                        "symbol": str(symbol_arr[idx]).upper(),
                        "exchange": str(exchange_arr[idx]).upper(),
                        "direction": str(side_arr[idx]).lower(),
                        "interval": normalize_portfolio_interval(selected.iloc[idx].get("interval", "")),
                    }
                )
            gate_df = pd.DataFrame(gate_rows)
            gate_out = htf_gate.filter(gate_df, htf_state=htf_state_now, current_time=pd.Timestamp(ts))
            allow_mask = gate_out.get("htf_allowed", pd.Series([True] * len(gate_out)))
            blocked_idx = gate_out.loc[~allow_mask, "_row_idx"].tolist()
            for ridx in blocked_idx:
                i = int(ridx)
                selected.at[i, "execution_status"] = "blocked_htf_gate"
                selected.at[i, "block_reason"] = str(
                    gate_out.loc[gate_out["_row_idx"] == ridx, "htf_block_reason"].astype(str).iloc[0]
                )
                selected.at[i, "equity_before"] = float(cash)
                selected.at[i, "equity_after"] = float(cash)
            entry_order = [int(x) for x in gate_out.loc[allow_mask, "_row_idx"].tolist()]
            for _, row in gate_out.iterrows():
                i = int(row["_row_idx"])
                selected.at[i, "htf_alignment"] = str(row.get("htf_alignment", "neutral"))

        if use_pl_ranker and ranker is not None and entry_order:
            cand_rows: list[dict[str, Any]] = []
            for idx in entry_order:
                prob_raw = pd.to_numeric(pd.Series([selected.iloc[idx].get("trade_filter_prob", np.nan)]), errors="coerce").iloc[0]
                prob_pctl = pd.to_numeric(
                    pd.Series([selected.iloc[idx].get("trade_filter_prob_pctl", np.nan)]),
                    errors="coerce",
                ).iloc[0]
                if not np.isfinite(prob_pctl):
                    prob_pctl = float(np.clip((float(prob_raw) if np.isfinite(prob_raw) else 0.0) * 100.0, 0.0, 100.0))
                cand_rows.append(
                    {
                        "_row_idx": int(idx),
                        "symbol": str(symbol_arr[idx]).upper(),
                        "exchange": str(exchange_arr[idx]).upper(),
                        "interval": normalize_portfolio_interval(selected.iloc[idx].get("interval", "")),
                        "direction": str(side_arr[idx]).lower(),
                        "cluster_name": infer_symbol_cluster(str(symbol_arr[idx]).upper()),
                        "trade_filter_prob_pctl": float(prob_pctl),
                        "pred_mfe_atr": float(pd.to_numeric(pd.Series([selected.iloc[idx].get("pred_mfe_atr", np.nan)]), errors="coerce").iloc[0]),
                        "pred_mae_atr": float(pd.to_numeric(pd.Series([selected.iloc[idx].get("pred_mae_atr", np.nan)]), errors="coerce").iloc[0]),
                        "htf_alignment": str(selected.iloc[idx].get("htf_alignment", "neutral") or "neutral"),
                        "base_notional": float(cash) * float(cfg.max_position_scale),
                    }
                )
            cand_df = pd.DataFrame(cand_rows)
            scored = ranker.score(cand_df, htf_state={})
            for _, row in scored.iterrows():
                i = int(row["_row_idx"])
                selected.at[i, "ranker_score"] = float(row.get("score", np.nan))

            state = PortfolioState(equity=float(cash))
            for pos in open_positions.values():
                sym = str(pos.get("symbol", ""))
                ex = str(pos.get("exchange", ""))
                side = str(pos.get("side", "")).lower()
                cluster = str(pos.get("cluster", infer_symbol_cluster(sym)))
                notional = max(0.0, float(pos.get("notional", 0.0) or 0.0))
                sym_key = (sym.upper(), ex.upper())
                state.total_positions += 1
                state.total_open_notional += notional
                state.symbol_counts[sym_key] = state.symbol_counts.get(sym_key, 0) + 1
                state.symbol_notional[sym_key] = state.symbol_notional.get(sym_key, 0.0) + notional
                state.cluster_counts[cluster] = state.cluster_counts.get(cluster, 0) + 1
                state.cluster_notional[cluster] = state.cluster_notional.get(cluster, 0.0) + notional
                state.open_symbol_direction.add((sym_key, side))

            caps_eff = pl_cfg.caps
            min_prob_pctl_for_bar = 0.0
            if use_pl_throttle and risk_throttle is not None and throttle_level is not None:
                caps_eff = risk_throttle.apply_to_caps(pl_cfg.caps, throttle_level)
                score_threshold_for_bar = max(
                    float(pl_cfg.ranker.score_threshold_baseline),
                    float(throttle_level.score_pctl_threshold) / 100.0,
                )
                min_prob_pctl_for_bar = float(getattr(throttle_level, "effective_min_prob_pctl", 0.0))
            else:
                score_threshold_for_bar = float(pl_cfg.ranker.score_threshold_baseline)
            picks = ranker.allocate(
                scored,
                state=state,
                caps=caps_eff,
                score_threshold=float(score_threshold_for_bar),
                min_prob_pctl=float(min_prob_pctl_for_bar),
            )
            selected_idx = {int(x) for x in picks.get("_row_idx", pd.Series([], dtype=int)).tolist()}
            for idx in entry_order:
                selected.at[idx, "ranker_score_threshold"] = float(score_threshold_for_bar)
                selected.at[idx, "ranker_prob_pctl_threshold"] = float(min_prob_pctl_for_bar)
                if int(idx) not in selected_idx:
                    selected.at[idx, "execution_status"] = "blocked_ranker"
                    selected.at[idx, "block_reason"] = "ranker_dropped"
                    selected.at[idx, "equity_before"] = float(cash)
                    selected.at[idx, "equity_after"] = float(cash)
            entry_order = [int(x) for x in picks.get("_row_idx", pd.Series([], dtype=int)).tolist()]

        for idx in entry_order:
            if str(selected.at[idx, "execution_status"]) != "pending":
                continue
            side_now = str(side_arr[idx]).strip().lower() if idx < len(side_arr) else ""
            selected.at[idx, "throttle_level_at_entry"] = throttle_level_name
            is_limit_up = bool(idx < len(limit_up_arr) and np.isfinite(limit_up_arr[idx]) and float(limit_up_arr[idx]) > 0.5)
            is_limit_down = bool(idx < len(limit_down_arr) and np.isfinite(limit_down_arr[idx]) and float(limit_down_arr[idx]) > 0.5)
            if (side_now == "long" and is_limit_up) or (side_now == "short" and is_limit_down):
                selected.at[idx, "execution_status"] = "blocked_limit_move"
                selected.at[idx, "block_reason"] = "blocked_limit_move"
                selected.at[idx, "equity_before"] = float(cash)
                selected.at[idx, "equity_after"] = float(cash)
                selected.at[idx, "position_scale"] = 0.0
                selected.at[idx, "position_notional"] = 0.0
                selected.at[idx, "entry_amount"] = 0.0
                selected.at[idx, "exit_amount"] = 0.0
                selected.at[idx, "max_loss_amount"] = 0.0
                selected.at[idx, "expected_loss_pct"] = 0.0
                selected.at[idx, "expected_loss_amount"] = 0.0
                continue
            entry_equity = float(cash)
            avail_cash = float(cash - margin_used)
            dd_before = float(entry_equity / week_peak_equity - 1.0) if week_peak_equity > 0 else float("nan")
            selected.at[idx, "available_cash_before_entry"] = avail_cash
            selected.at[idx, "margin_used_before_entry"] = float(margin_used)
            selected.at[idx, "open_notional_before_entry"] = float(open_notional)
            selected.at[idx, "weekly_drawdown_pct_before_entry"] = dd_before

            pred_mae = float(pred_mae_arr[idx]) if np.isfinite(pred_mae_arr[idx]) else float(cfg.min_pred_mae_atr_for_sizing)
            pred_mae = max(pred_mae, float(cfg.min_pred_mae_atr_for_sizing))
            stop_risk_pct = float("nan")
            ent_fill = float(entry_fill_arr[idx]) if np.isfinite(entry_fill_arr[idx]) else float("nan")
            sl_price = float(stop_loss_arr[idx]) if np.isfinite(stop_loss_arr[idx]) else float("nan")
            if np.isfinite(ent_fill) and ent_fill > 0 and np.isfinite(sl_price) and sl_price > 0:
                stop_risk_pct = abs(ent_fill - sl_price) / ent_fill
            else:
                ent_px = float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else float("nan")
                if np.isfinite(ent_px) and ent_px > 0 and np.isfinite(sl_price) and sl_price > 0:
                    stop_risk_pct = abs(ent_px - sl_price) / ent_px

            if bool(cfg.use_position_sizing):
                if np.isfinite(stop_risk_pct) and stop_risk_pct > 0:
                    expected_loss_pct = float(stop_risk_pct)
                else:
                    expected_loss_pct = float(pred_mae) * float(cfg.risk_per_trade_pct)
                if expected_loss_pct > 0:
                    position_scale = float(min(float(cfg.max_position_scale), float(cfg.max_single_loss_pct) / expected_loss_pct))
                else:
                    position_scale = float(min(float(cfg.max_position_scale), 1.0))
            else:
                expected_loss_pct = float(cfg.max_single_loss_pct)
                position_scale = float(min(float(cfg.max_position_scale), 1.0))
            if week_dd_breached:
                position_scale = float(position_scale * float(cfg.weekly_dd_position_scale_after_breach))
            position_scale = max(position_scale, 0.0)
            desired_notional = max(0.0, float(entry_equity * position_scale))

            margin_rate = max(float(cfg.margin_rate), 1e-9)
            notional = desired_notional
            reason = ""

            sym_key = str(symbol_arr[idx]) if idx < len(symbol_arr) else ""
            ex_key = str(exchange_arr[idx]) if idx < len(exchange_arr) else ""
            cluster_key = infer_symbol_cluster(sym_key)
            sym_dir_key = (sym_key, ex_key, side_now)
            sym_notional_now = float(symbol_open_notional.get(sym_key, 0.0))
            sym_count_now = int(symbol_open_count.get(sym_key, 0))
            cluster_notional_now = float(cluster_open_notional.get(cluster_key, 0.0))
            cluster_count_now = int(cluster_open_count.get(cluster_key, 0))
            total_count_now = int(len(open_positions))

            is_add_layer = False
            current_pos_obj: PyramidPosition | None = None
            if use_pl_pyramid and pyramid_manager is not None:
                current_pos_obj = active_pyramid_by_sym_dir.get(sym_dir_key)
                if current_pos_obj is not None:
                    ent_for_add = float(ent_fill) if np.isfinite(ent_fill) else float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else 0.0
                    can_add = pyramid_manager.decide_add_layer(
                        pos=current_pos_obj,
                        new_interval=normalize_portfolio_interval(selected.iloc[idx].get("interval", "")),
                        current_time=pd.Timestamp(ts),
                        current_price=ent_for_add,
                        min_profit_atr_to_add=float(pl_cfg.pyramid.min_profit_atr_to_add),
                        htf_aligned=True,
                    )
                    if not can_add:
                        selected.at[idx, "execution_status"] = "blocked_pyramid_rule"
                        selected.at[idx, "block_reason"] = "blocked_pyramid_rule"
                        selected.at[idx, "equity_before"] = float(cash)
                        selected.at[idx, "equity_after"] = float(cash)
                        selected.at[idx, "position_scale"] = 0.0
                        selected.at[idx, "position_notional"] = 0.0
                        selected.at[idx, "entry_amount"] = 0.0
                        selected.at[idx, "exit_amount"] = 0.0
                        selected.at[idx, "max_loss_amount"] = 0.0
                        selected.at[idx, "expected_loss_pct"] = expected_loss_pct
                        selected.at[idx, "expected_loss_amount"] = 0.0
                        continue
                    is_add_layer = True

            if bool(cfg.use_portfolio_constraints):
                if bool(cfg.block_new_entries_on_monthly_dd_breach) and month_dd_breached:
                    notional = 0.0
                    reason = "blocked_monthly_drawdown"
                elif bool(cfg.block_new_entries_on_weekly_dd_breach) and week_dd_breached:
                    notional = 0.0
                    reason = "blocked_weekly_drawdown"
                elif total_count_now >= int(cfg.max_concurrent_positions_total):
                    notional = 0.0
                    reason = "blocked_total_concurrent"
                elif (not use_pl_pyramid) and sym_key and sym_count_now >= int(cfg.max_concurrent_positions_per_symbol):
                    notional = 0.0
                    reason = "blocked_symbol_concurrent"
                else:
                    cap_daily = max(0.0, float(day_start_equity * float(cfg.max_daily_new_notional_pct) - day_new_notional))
                    cap_lev = max(0.0, float(entry_equity * float(cfg.max_total_leverage) - open_notional))
                    cap_cash = max(0.0, float(avail_cash / margin_rate))
                    cap_symbol = max(0.0, float(entry_equity * float(cfg.max_symbol_notional_pct) - sym_notional_now))
                    cap_cluster = float("inf")
                    if use_pl_runtime and pl_cfg is not None:
                        cap_symbol = max(0.0, float(entry_equity * float(pl_cfg.caps.max_symbol_notional_pct) - sym_notional_now))
                        cap_cluster = max(0.0, float(entry_equity * float(pl_cfg.caps.max_cluster_notional_pct) - cluster_notional_now))
                    cap_week = float("inf")
                    if bool(cfg.enforce_weekly_dd_budget_on_entry) and float(cfg.weekly_max_drawdown_pct) > 0:
                        used_dd_amt = max(0.0, float(week_peak_equity - entry_equity))
                        remain_dd_amt = max(0.0, float(week_peak_equity * float(cfg.weekly_max_drawdown_pct) - used_dd_amt))
                        if float(cfg.max_single_loss_pct) > 0:
                            cap_week = float(remain_dd_amt / float(cfg.max_single_loss_pct))
                        else:
                            cap_week = 0.0
                    notional = float(min(notional, cap_daily, cap_lev, cap_cash, cap_week, cap_symbol, cap_cluster))
                    if notional <= 0:
                        if cap_symbol <= 0:
                            reason = "blocked_symbol_cap"
                        elif cap_cluster <= 0:
                            reason = "blocked_symbol_cap"
                        elif cap_week <= 0:
                            reason = "blocked_weekly_budget"
                        elif cap_daily <= 0:
                            reason = "blocked_daily_position"
                        elif cap_cash <= 0:
                            reason = "blocked_margin_cash"
                        elif cap_lev <= 0:
                            reason = "blocked_leverage"
                        else:
                            reason = "blocked_portfolio_constraint"

            if notional <= 0:
                selected.at[idx, "execution_status"] = reason or "blocked_zero_notional"
                selected.at[idx, "block_reason"] = reason or "zero_notional"
                selected.at[idx, "equity_before"] = entry_equity
                selected.at[idx, "equity_after"] = entry_equity
                selected.at[idx, "position_scale"] = 0.0
                selected.at[idx, "position_notional"] = 0.0
                selected.at[idx, "entry_amount"] = 0.0
                selected.at[idx, "exit_amount"] = 0.0
                selected.at[idx, "max_loss_amount"] = 0.0
                selected.at[idx, "expected_loss_pct"] = expected_loss_pct
                selected.at[idx, "expected_loss_amount"] = 0.0
                continue

            margin = float(notional * margin_rate)
            margin_used = float(margin_used + margin)
            open_notional = float(open_notional + notional)
            day_new_notional = float(day_new_notional + notional)
            if sym_key:
                symbol_open_notional[sym_key] = float(symbol_open_notional.get(sym_key, 0.0) + notional)
                symbol_open_count[sym_key] = int(symbol_open_count.get(sym_key, 0) + 1)
            cluster_open_notional[cluster_key] = float(cluster_open_notional.get(cluster_key, 0.0) + notional)
            cluster_open_count[cluster_key] = int(cluster_open_count.get(cluster_key, 0) + 1)

            max_loss_amount = float(entry_equity * float(cfg.max_single_loss_pct))
            expected_loss_amount = float(notional * float(expected_loss_pct))
            entry_price = float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else float("nan")
            position_qty = float(notional / entry_price) if np.isfinite(entry_price) and entry_price > 0 else float("nan")

            selected.at[idx, "execution_status"] = "opened"
            selected.at[idx, "equity_before"] = entry_equity
            selected.at[idx, "position_scale"] = float(notional / entry_equity) if entry_equity > 0 else 0.0
            selected.at[idx, "position_notional"] = notional
            # 买入成交后组合总持仓资金（含当前这笔）
            selected.at[idx, "open_notional_at_entry"] = float(open_notional)
            selected.at[idx, "max_loss_amount"] = max_loss_amount
            selected.at[idx, "position_qty"] = position_qty
            selected.at[idx, "expected_loss_pct"] = expected_loss_pct
            selected.at[idx, "expected_loss_amount"] = expected_loss_amount

            if use_pl_pyramid and pyramid_manager is not None:
                if is_add_layer and current_pos_obj is not None:
                    new_layer = pyramid_manager.add_layer(
                        pos=current_pos_obj,
                        interval=normalize_portfolio_interval(selected.iloc[idx].get("interval", "")),
                        entry_time=pd.Timestamp(ts),
                        entry_price=float(entry_price) if np.isfinite(entry_price) else float(ent_fill) if np.isfinite(ent_fill) else 0.0,
                        notional=float(notional),
                        atr_pct_at_entry=float(atr_pct_arr[idx]) if idx < len(atr_pct_arr) and np.isfinite(atr_pct_arr[idx]) else float("nan"),
                        signal_score=float(pd.to_numeric(pd.Series([selected.iloc[idx].get("ranker_score", np.nan)]), errors="coerce").fillna(0.0).iloc[0]),
                        trade_filter_prob=float(pd.to_numeric(pd.Series([selected.iloc[idx].get("trade_filter_prob", np.nan)]), errors="coerce").fillna(0.0).iloc[0]),
                        trade_filter_prob_pctl=float(pd.to_numeric(pd.Series([selected.iloc[idx].get("trade_filter_prob_pctl", np.nan)]), errors="coerce").fillna(0.0).iloc[0]),
                        trailing_cfg=pl_cfg.trailing,
                    )
                    pos_obj = current_pos_obj
                    layer_id = int(new_layer.layer_id)
                else:
                    pos_obj = pyramid_manager.open_first_layer(
                        symbol=sym_key,
                        exchange=ex_key,
                        direction=side_now,
                        interval=normalize_portfolio_interval(selected.iloc[idx].get("interval", "")),
                        entry_time=pd.Timestamp(ts),
                        entry_price=float(entry_price) if np.isfinite(entry_price) else float(ent_fill) if np.isfinite(ent_fill) else 0.0,
                        notional=float(notional),
                        atr_pct_at_entry=float(atr_pct_arr[idx]) if idx < len(atr_pct_arr) and np.isfinite(atr_pct_arr[idx]) else float("nan"),
                        signal_score=float(pd.to_numeric(pd.Series([selected.iloc[idx].get("ranker_score", np.nan)]), errors="coerce").fillna(0.0).iloc[0]),
                        trade_filter_prob=float(pd.to_numeric(pd.Series([selected.iloc[idx].get("trade_filter_prob", np.nan)]), errors="coerce").fillna(0.0).iloc[0]),
                        trade_filter_prob_pctl=float(pd.to_numeric(pd.Series([selected.iloc[idx].get("trade_filter_prob_pctl", np.nan)]), errors="coerce").fillna(0.0).iloc[0]),
                        trailing_cfg=pl_cfg.trailing,
                    )
                    active_pyramid_by_sym_dir[sym_dir_key] = pos_obj
                    layer_id = 0
                selected.at[idx, "pos_id"] = str(pos_obj.pos_id)
                selected.at[idx, "layer_id"] = int(layer_id)
                selected.at[idx, "layer_interval"] = normalize_portfolio_interval(selected.iloc[idx].get("interval", ""))
                idx_layer_ref[idx] = (pos_obj, int(layer_id))
            else:
                selected.at[idx, "pos_id"] = f"{sym_key}_{ex_key}_{side_now}_{pd.Timestamp(ts).isoformat()}"
                selected.at[idx, "layer_id"] = 0
                selected.at[idx, "layer_interval"] = normalize_portfolio_interval(selected.iloc[idx].get("interval", ""))

            open_positions[idx] = {
                "notional": notional,
                "margin": margin,
                "gross_ret_pct": float(gross_pct_arr[idx]),
                "net_ret_pct": float(net_pct_arr[idx]),
                "entry_equity": entry_equity,
                "symbol": sym_key,
                "exchange": ex_key,
                "side": side_now,
                "cluster": cluster_key,
                "pos_id": str(selected.at[idx, "pos_id"]),
                "layer_id": int(pd.to_numeric(pd.Series([selected.at[idx, "layer_id"]]), errors="coerce").fillna(0).iloc[0]),
                "entry_ts": pd.Timestamp(ts),
            }

    # Any not-closed position by timeline end is force-closed with provided trade return.
    for idx, pos in list(open_positions.items()):
        margin_used = max(0.0, float(margin_used - float(pos["margin"])))
        open_notional = max(0.0, float(open_notional - float(pos["notional"])))
        pos_sym = str(pos.get("symbol", ""))
        if pos_sym:
            symbol_open_notional[pos_sym] = max(
                0.0, float(symbol_open_notional.get(pos_sym, 0.0) - float(pos["notional"]))
            )
            symbol_open_count[pos_sym] = max(0, int(symbol_open_count.get(pos_sym, 0) - 1))
        pos_cluster = str(pos.get("cluster", "other"))
        cluster_open_notional[pos_cluster] = max(
            0.0, float(cluster_open_notional.get(pos_cluster, 0.0) - float(pos["notional"]))
        )
        cluster_open_count[pos_cluster] = max(0, int(cluster_open_count.get(pos_cluster, 0) - 1))
        if use_pl_pyramid and idx in idx_layer_ref:
            pos_ref, layer_id_ref = idx_layer_ref.pop(idx)
            for layer in pos_ref.layers:
                if int(layer.layer_id) == int(layer_id_ref):
                    layer.exited = True
                    break
            sym_dir = (pos_ref.symbol, pos_ref.exchange, pos_ref.direction)
            if len(pos_ref.active_layers) == 0:
                active_pyramid_by_sym_dir.pop(sym_dir, None)
        gross_pnl = float(pos["notional"]) * float(pos["gross_ret_pct"])
        fallback_exit_ts = pd.to_datetime(selected.at[idx, "exit_datetime"], errors="coerce")
        if pd.isna(fallback_exit_ts):
            fallback_exit_ts = pd.Timestamp(pos.get("entry_ts", pd.Timestamp.now()))
        roll_cost = _calc_roll_cost(
            symbol=pos_sym,
            notional=float(pos["notional"]),
            entry_ts=pd.Timestamp(pos.get("entry_ts", fallback_exit_ts)),
            exit_ts=pd.Timestamp(fallback_exit_ts),
            cfg=cfg,
        )
        net_pnl = float(pos["notional"]) * float(pos["net_ret_pct"]) - float(roll_cost)
        cash = float(cash + net_pnl)
        selected.at[idx, "equity_before"] = float(pos["entry_equity"])
        selected.at[idx, "equity_after"] = float(cash)
        selected.at[idx, "gross_pnl"] = gross_pnl
        selected.at[idx, "net_pnl"] = net_pnl
        selected.at[idx, "roll_cost"] = float(roll_cost)
        selected.at[idx, "pnl_amount"] = net_pnl
        selected.at[idx, "entry_amount"] = float(pos["notional"])
        selected.at[idx, "exit_amount"] = float(pos["notional"] + net_pnl)
        selected.at[idx, "execution_status"] = "executed"
    open_positions.clear()

    executed = selected.loc[selected["execution_status"] == "executed"].copy()
    if executed.empty:
        summary = pd.DataFrame(
            [
                {
                    "oot_rows": oot_rows,
                    "executed_rows": executed_rows,
                    "trade_count": 0,
                    "selected_rows": selected_rows,
                    "blocked_rows": int((selected["execution_status"] != "executed").sum()),
                    "blocked_margin_cash_rows": int((selected["execution_status"] == "blocked_margin_cash").sum()),
                    "blocked_leverage_rows": int((selected["execution_status"] == "blocked_leverage").sum()),
                    "blocked_limit_move_rows": int((selected["execution_status"] == "blocked_limit_move").sum()),
                    "blocked_daily_position_rows": int((selected["execution_status"] == "blocked_daily_position").sum()),
                    "blocked_weekly_drawdown_rows": int((selected["execution_status"] == "blocked_weekly_drawdown").sum()),
                    "blocked_weekly_budget_rows": int((selected["execution_status"] == "blocked_weekly_budget").sum()),
                    "blocked_monthly_drawdown_rows": int(
                        (selected["execution_status"] == "blocked_monthly_drawdown").sum()
                    ),
                    "blocked_symbol_cap_rows": int((selected["execution_status"] == "blocked_symbol_cap").sum()),
                    "blocked_symbol_concurrent_rows": int((selected["execution_status"] == "blocked_symbol_concurrent").sum()),
                    "blocked_total_concurrent_rows": int((selected["execution_status"] == "blocked_total_concurrent").sum()),
                    "blocked_htf_rows": int((selected["execution_status"] == "blocked_htf_gate").sum()),
                    "blocked_ranker_rows": int((selected["execution_status"] == "blocked_ranker").sum()),
                    "blocked_throttle_rows": int((selected["execution_status"] == "blocked_throttle_halt").sum()),
                    "blocked_pyramid_rows": int((selected["execution_status"] == "blocked_pyramid_rule").sum()),
                    "stop_loss_exit_rows": 0,
                    "trailing_stop_exit_rows": 0,
                    "horizon_exit_rows": 0,
                    "monthly_obs": 0,
                    "gross_pnl": 0.0,
                    "net_pnl": 0.0,
                    "roll_cost_total": 0.0,
                    "total_return_pct": 0.0,
                    "avg_monthly_return_pct": float("nan"),
                    "std_monthly_return_pct": float("nan"),
                    "monthly_excess_return_pct": float("nan"),
                    "std_monthly_excess_return_pct": float("nan"),
                    "monthly_sharpe": float("nan"),
                    "max_drawdown_pct": float("nan"),
                    "annualized_return_pct": float("nan"),
                    "calmar_like": float("nan"),
                }
            ]
        )
        trade_df = selected.loc[:, [c for c in trade_cols if c in selected.columns]].copy()
        if extra_outputs is not None:
            extra_outputs["throttle_log"] = pd.DataFrame(throttle_log_rows, columns=throttle_cols)
            extra_outputs["position_lifetime"] = _build_position_lifetime_table(trade_df)
        return pd.DataFrame(columns=monthly_cols), summary[summary_cols], trade_df

    executed["month"] = pd.to_datetime(executed["exit_datetime"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    monthly = (
        executed.groupby("month", as_index=False)
        .agg(
            trade_count=("net_pnl", "size"),
            win_count=("net_pnl", lambda s: int((pd.Series(s) > 0.0).sum())),
            loss_count=("net_pnl", lambda s: int((pd.Series(s) < 0.0).sum())),
            win_rate=("net_pnl", lambda s: float((pd.Series(s) > 0.0).mean())),
            gross_pnl=("gross_pnl", "sum"),
            net_pnl=("net_pnl", "sum"),
        )
        .sort_values("month")
        .reset_index(drop=True)
    )
    start_cap = float(cfg.initial_capital)
    month_start_arr: list[float] = []
    month_end_arr: list[float] = []
    cur_eq = start_cap
    for _, r in monthly.iterrows():
        month_start_arr.append(float(cur_eq))
        net_v = float(pd.to_numeric(pd.Series([r.get("net_pnl", 0.0)]), errors="coerce").fillna(0.0).iloc[0])
        cur_eq = float(cur_eq + net_v)
        month_end_arr.append(float(cur_eq))
    monthly["month_start_equity"] = month_start_arr
    monthly["month_end_equity"] = month_end_arr
    monthly["monthly_return_pct"] = (
        pd.to_numeric(monthly["month_end_equity"], errors="coerce")
        / pd.to_numeric(monthly["month_start_equity"], errors="coerce")
        - 1.0
    )
    benchmark_m = (1.0 + float(cfg.benchmark_annual_return)) ** (1.0 / float(cfg.annualization_factor)) - 1.0
    monthly["monthly_excess_return_pct"] = pd.to_numeric(monthly["monthly_return_pct"], errors="coerce") - float(benchmark_m)
    start_cap = float(cfg.initial_capital)
    monthly["cum_return_pct"] = pd.to_numeric(monthly["month_end_equity"], errors="coerce") / start_cap - 1.0

    gross = float(pd.to_numeric(monthly["gross_pnl"], errors="coerce").fillna(0.0).sum())
    net = float(pd.to_numeric(monthly["net_pnl"], errors="coerce").fillna(0.0).sum())
    roll_total = float(pd.to_numeric(executed.get("roll_cost", 0.0), errors="coerce").fillna(0.0).sum())
    exit_reason_series = executed.get("exit_reason", pd.Series([""] * len(executed), index=executed.index)).astype(str)
    stop_loss_exit_rows = int(exit_reason_series.isin(["stop_loss", "hard_stop"]).sum())
    trailing_stop_exit_rows = int((exit_reason_series == "trailing_stop").sum())
    horizon_exit_rows = int((exit_reason_series == "horizon_exit").sum())
    mean_m = float(pd.to_numeric(monthly["monthly_return_pct"], errors="coerce").mean()) if len(monthly) else float("nan")
    std_m = (
        float(pd.to_numeric(monthly["monthly_return_pct"], errors="coerce").std(ddof=1))
        if len(monthly) >= 2
        else float("nan")
    )
    mean_ex = float(pd.to_numeric(monthly["monthly_excess_return_pct"], errors="coerce").mean()) if len(monthly) else float("nan")
    std_ex = (
        float(pd.to_numeric(monthly["monthly_excess_return_pct"], errors="coerce").std(ddof=1))
        if len(monthly) >= 2
        else float("nan")
    )
    if np.isfinite(mean_ex) and np.isfinite(std_ex) and std_ex > 0:
        sharpe = float(mean_ex / std_ex * np.sqrt(float(cfg.annualization_factor)))
    else:
        sharpe = float("nan")
    eq = pd.to_numeric(monthly["month_end_equity"], errors="coerce").ffill().fillna(start_cap)
    trade_eq = (
        pd.to_numeric(
            executed.sort_values("exit_datetime")["equity_after"],
            errors="coerce",
        )
        .ffill()
        .fillna(start_cap)
    )
    dd = trade_eq / trade_eq.cummax() - 1.0
    max_dd = float(dd.min()) if not dd.empty else float("nan")
    ann = (float((1.0 + mean_m) ** float(cfg.annualization_factor) - 1.0) if np.isfinite(mean_m) else float("nan"))
    if np.isfinite(ann) and np.isfinite(max_dd) and max_dd < 0:
        calmar_like = float(ann / abs(max_dd))
    else:
        calmar_like = float("nan")
    total_return_pct = (float(eq.iloc[-1] / start_cap - 1.0) if not eq.empty else float("nan"))

    summary = pd.DataFrame(
        [
            {
                "oot_rows": oot_rows,
                "executed_rows": executed_rows,
                "trade_count": int(len(executed)),
                "selected_rows": selected_rows,
                "blocked_rows": int((selected["execution_status"] != "executed").sum()),
                "blocked_margin_cash_rows": int((selected["execution_status"] == "blocked_margin_cash").sum()),
                "blocked_leverage_rows": int((selected["execution_status"] == "blocked_leverage").sum()),
                "blocked_limit_move_rows": int((selected["execution_status"] == "blocked_limit_move").sum()),
                "blocked_daily_position_rows": int((selected["execution_status"] == "blocked_daily_position").sum()),
                "blocked_weekly_drawdown_rows": int((selected["execution_status"] == "blocked_weekly_drawdown").sum()),
                "blocked_weekly_budget_rows": int((selected["execution_status"] == "blocked_weekly_budget").sum()),
                "blocked_monthly_drawdown_rows": int(
                    (selected["execution_status"] == "blocked_monthly_drawdown").sum()
                ),
                "blocked_symbol_cap_rows": int((selected["execution_status"] == "blocked_symbol_cap").sum()),
                "blocked_symbol_concurrent_rows": int((selected["execution_status"] == "blocked_symbol_concurrent").sum()),
                "blocked_total_concurrent_rows": int((selected["execution_status"] == "blocked_total_concurrent").sum()),
                "blocked_htf_rows": int((selected["execution_status"] == "blocked_htf_gate").sum()),
                "blocked_ranker_rows": int((selected["execution_status"] == "blocked_ranker").sum()),
                "blocked_throttle_rows": int((selected["execution_status"] == "blocked_throttle_halt").sum()),
                "blocked_pyramid_rows": int((selected["execution_status"] == "blocked_pyramid_rule").sum()),
                "stop_loss_exit_rows": stop_loss_exit_rows,
                "trailing_stop_exit_rows": trailing_stop_exit_rows,
                "horizon_exit_rows": horizon_exit_rows,
                "monthly_obs": int(len(monthly)),
                "gross_pnl": gross,
                "net_pnl": net,
                "roll_cost_total": roll_total,
                "total_return_pct": total_return_pct,
                "avg_monthly_return_pct": mean_m,
                "std_monthly_return_pct": std_m,
                "monthly_excess_return_pct": mean_ex,
                "std_monthly_excess_return_pct": std_ex,
                "monthly_sharpe": sharpe,
                "max_drawdown_pct": max_dd,
                "annualized_return_pct": ann,
                "calmar_like": calmar_like,
            }
        ]
    )
    trade_df = selected.loc[:, [c for c in trade_cols if c in selected.columns]].copy()
    if extra_outputs is not None:
        extra_outputs["throttle_log"] = pd.DataFrame(throttle_log_rows, columns=throttle_cols)
        extra_outputs["position_lifetime"] = _build_position_lifetime_table(trade_df)
    return monthly[monthly_cols], summary[summary_cols], trade_df


__all__ = ["_evaluate_oot_real_execution"]
