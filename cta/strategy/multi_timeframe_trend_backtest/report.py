"""Auditable report bundle for the multi-timeframe trend backtest."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from cta.strategy.brooks.cycle_v1.backtest.reporter import (
    build_funnel,
    build_group_report,
    build_official_summary,
    compute_performance_metrics,
)

from .engine import (
    MARKET_LIQUIDITY_COLUMNS,
    MARKET_LIQUIDITY_WINDOWS,
    TRADE_COLUMNS,
    ReplayArtifacts,
)


FEE_AUDIT_COLUMNS = (
    "candidate_id",
    "symbol",
    "contract_code",
    "quantity",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "open_fee",
    "close_fee",
    "fees",
    "close_type",
    "entry_fee_rate",
    "exit_fee_rate",
    "entry_fixed_fee",
    "exit_fixed_fee",
    "fee_stress_multiplier",
    "entry_fee_schedule_id",
    "exit_fee_schedule_id",
    "entry_metadata_hash",
    "exit_metadata_hash",
    "contract_multiplier",
    "entry_fee_source",
    "exit_fee_source",
    "entry_fee_effective_from",
    "exit_fee_effective_from",
    "entry_fee_known_at",
    "exit_fee_known_at",
)
SYMBOL_PERFORMANCE_COLUMNS = (
    "symbol",
    "trade_count",
    "winning_trade_count",
    "losing_trade_count",
    "breakeven_trade_count",
    "win_rate_pct",
    "total_gross_pnl",
    "total_net_pnl",
    "total_return_pct",
    "max_drawdown_amount",
    "max_drawdown_pct",
    "profit_factor",
    "average_win_loss_ratio",
    "average_win",
    "average_loss",
    "expectancy_R",
    "fees",
    "slippage",
    "turnover",
)


def _with_market_liquidity(
    trades: pd.DataFrame,
    *,
    daily_market_bars: pd.DataFrame,
    entry_trade_dates: pd.DataFrame,
) -> pd.DataFrame:
    """Add causal pre-entry market activity averages to logical trades."""
    result = trades.copy()
    for column in MARKET_LIQUIDITY_COLUMNS:
        result[column] = math.nan
    if result.empty or daily_market_bars.empty or entry_trade_dates.empty:
        return result.reindex(columns=TRADE_COLUMNS)

    required_daily = {
        "symbol", "exchange_trade_date", "volume", "turnover",
    }
    missing_daily = sorted(required_daily.difference(daily_market_bars.columns))
    if missing_daily:
        raise ValueError(
            "daily market bars are missing: " + ",".join(missing_daily)
        )
    required_dates = {"candidate_id", "exchange_trade_date"}
    missing_dates = sorted(required_dates.difference(entry_trade_dates.columns))
    if missing_dates:
        raise ValueError(
            "entry trade dates are missing: " + ",".join(missing_dates)
        )

    dates = entry_trade_dates.loc[:, ["candidate_id", "exchange_trade_date"]].copy()
    if dates["candidate_id"].duplicated().any():
        raise ValueError("entry trade dates require unique candidate_id")
    dates["exchange_trade_date"] = pd.to_datetime(
        dates["exchange_trade_date"], errors="raise"
    ).dt.date
    date_lookup = dates.set_index("candidate_id")["exchange_trade_date"]

    daily = daily_market_bars.loc[:, list(required_daily)].copy()
    daily["symbol"] = daily["symbol"].astype(str).str.strip()
    daily["exchange_trade_date"] = pd.to_datetime(
        daily["exchange_trade_date"], errors="raise"
    ).dt.date
    if daily.duplicated(["symbol", "exchange_trade_date"]).any():
        raise ValueError("daily market bars require one row per symbol and trade date")
    for column in ("volume", "turnover"):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily = daily.sort_values(
        ["symbol", "exchange_trade_date"], kind="stable"
    ).reset_index(drop=True)

    for index, trade in result.iterrows():
        candidate_id = trade.get("candidate_id")
        if candidate_id not in date_lookup.index:
            continue
        entry_trade_date = date_lookup.loc[candidate_id]
        symbol = str(trade.get("symbol", "")).strip()
        prior = daily.loc[
            daily["symbol"].eq(symbol)
            & daily["exchange_trade_date"].lt(entry_trade_date)
        ]
        for days in MARKET_LIQUIDITY_WINDOWS:
            window = prior.tail(days)
            if len(window) != days:
                continue
            for source in ("volume", "turnover"):
                values = window[source]
                if values.notna().all() and np.isfinite(values.to_numpy(float)).all():
                    result.at[index, f"prior_{days}d_avg_market_{source}"] = (
                        float(values.mean())
                    )
    return result.reindex(columns=TRADE_COLUMNS)


def _build_symbol_performance(
    trades: pd.DataFrame,
    *,
    symbols: Sequence[str],
    initial_equity: float,
) -> pd.DataFrame:
    """Summarize realized logical-trade performance for each root symbol."""
    if not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be finite and positive")
    trade_symbols = (
        set(trades["symbol"].dropna().astype(str).str.strip())
        if "symbol" in trades
        else set()
    )
    universe = sorted(
        {str(symbol).strip() for symbol in symbols if str(symbol).strip()}
        | {symbol for symbol in trade_symbols if symbol}
    )
    rows: list[dict[str, Any]] = []
    for symbol in universe:
        symbol_trades = (
            trades.loc[trades["symbol"].astype(str).str.strip().eq(symbol)].copy()
            if "symbol" in trades
            else pd.DataFrame()
        )
        if symbol_trades.empty:
            rows.append(_empty_symbol_performance_row(symbol))
            continue
        required = {
            "candidate_id", "exit_time", "gross_pnl", "net_pnl", "net_r",
            "fees", "slippage", "turnover",
        }
        missing = sorted(required.difference(symbol_trades.columns))
        if missing:
            raise ValueError(
                "symbol performance trades are missing: " + ",".join(missing)
            )
        ordered = symbol_trades.sort_values(
            ["exit_time", "candidate_id"], kind="stable"
        )
        pnl = pd.to_numeric(ordered["net_pnl"], errors="raise")
        gross = pd.to_numeric(ordered["gross_pnl"], errors="raise")
        wins = pnl.loc[pnl > 0]
        losses = pnl.loc[pnl < 0]
        curve = np.concatenate(([0.0], pnl.cumsum().to_numpy(float)))
        drawdowns = np.maximum.accumulate(curve) - curve
        max_drawdown_amount = float(drawdowns.max())
        gross_profit = float(wins.sum())
        gross_loss = abs(float(losses.sum()))
        average_win = float(wins.mean()) if not wins.empty else math.nan
        average_loss = float(losses.mean()) if not losses.empty else math.nan
        rows.append(
            {
                "symbol": symbol,
                "trade_count": len(ordered),
                "winning_trade_count": int((pnl > 0).sum()),
                "losing_trade_count": int((pnl < 0).sum()),
                "breakeven_trade_count": int((pnl == 0).sum()),
                "win_rate_pct": 100.0 * float((pnl > 0).mean()),
                "total_gross_pnl": float(gross.sum()),
                "total_net_pnl": float(pnl.sum()),
                "total_return_pct": 100.0 * float(pnl.sum()) / initial_equity,
                "max_drawdown_amount": max_drawdown_amount,
                "max_drawdown_pct": (
                    100.0 * max_drawdown_amount / initial_equity
                ),
                "profit_factor": (
                    gross_profit / gross_loss if gross_loss > 0 else math.nan
                ),
                "average_win_loss_ratio": (
                    average_win / abs(average_loss)
                    if math.isfinite(average_win)
                    and math.isfinite(average_loss)
                    and average_loss < 0
                    else math.nan
                ),
                "average_win": average_win,
                "average_loss": average_loss,
                "expectancy_R": float(
                    pd.to_numeric(ordered["net_r"], errors="raise").mean()
                ),
                "fees": float(
                    pd.to_numeric(ordered["fees"], errors="raise").sum()
                ),
                "slippage": float(
                    pd.to_numeric(ordered["slippage"], errors="raise").sum()
                ),
                "turnover": float(
                    pd.to_numeric(ordered["turnover"], errors="raise").sum()
                ),
            }
        )
    return pd.DataFrame(rows, columns=SYMBOL_PERFORMANCE_COLUMNS)


def _empty_symbol_performance_row(symbol: str) -> dict[str, Any]:
    row = {column: math.nan for column in SYMBOL_PERFORMANCE_COLUMNS}
    row.update(
        {
            "symbol": symbol,
            "trade_count": 0,
            "winning_trade_count": 0,
            "losing_trade_count": 0,
            "breakeven_trade_count": 0,
            "total_gross_pnl": 0.0,
            "total_net_pnl": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_amount": 0.0,
            "max_drawdown_pct": 0.0,
            "fees": 0.0,
            "slippage": 0.0,
            "turnover": 0.0,
        }
    )
    return row


def _render_daily_equity_chart(
    path: str | Path,
    *,
    daily_equity: pd.DataFrame,
    initial_equity: float,
) -> Path:
    """Render daily equity as a percentage of initial capital."""
    if not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be finite and positive")
    output = Path(path)
    curve = daily_equity.loc[:, ["date", "equity"]].copy()
    tick_dates = pd.DatetimeIndex([])
    if not curve.empty:
        curve["date"] = pd.to_datetime(curve["date"], errors="raise")
        curve["equity"] = pd.to_numeric(curve["equity"], errors="raise")
        if not np.isfinite(curve["equity"].to_numpy(float)).all():
            raise ValueError("daily equity must be finite")
        curve = curve.sort_values("date", kind="stable").reset_index(drop=True)
        tick_dates = pd.date_range(
            curve["date"].iloc[0].normalize(),
            curve["date"].iloc[-1].normalize(),
            freq="5D",
        )

    left, top, right_margin, bottom_margin = 110, 70, 40, 85
    plot_width = max(1_400, max(1, len(tick_dates) - 1) * 80)
    width = min(16_000, left + plot_width + right_margin)
    height = 780
    right = width - right_margin
    bottom = height - bottom_margin
    image = Image.new("RGB", (width, height), "#f5f0e6")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rectangle((left, top, right, bottom), fill="#fbf8f1")

    equity_pct = (
        100.0 * curve["equity"] / initial_equity
        if not curve.empty
        else pd.Series(dtype=float)
    )
    values = np.append(equity_pct.to_numpy(float), 100.0)
    minimum = float(values.min())
    maximum = float(values.max())
    padding = max((maximum - minimum) * 0.08, max(abs(maximum), 1.0) * 0.01)
    lower = minimum - padding
    upper = maximum + padding

    def y_position(value: float) -> int:
        return int(round(bottom - (value - lower) / (upper - lower) * (bottom - top)))

    for value in np.linspace(lower, upper, 9):
        y = y_position(float(value))
        for x in range(left, right, 8):
            draw.line((x, y, min(x + 3, right), y), fill="#d7d0c4", width=1)
        label = f"{value:.2f}%"
        draw.text((left - 8, y), label, fill="#625b4f", font=font, anchor="rm")

    baseline_y = y_position(100.0)
    for x in range(left, right, 16):
        draw.line(
            (x, baseline_y, min(x + 8, right), baseline_y),
            fill="#8b5e34",
            width=1,
        )

    if daily_equity.empty:
        draw.text(
            ((left + right) // 2, (top + bottom) // 2),
            "No daily equity data",
            fill="#625b4f",
            font=font,
            anchor="mm",
        )
    else:
        first_date = curve["date"].iloc[0]
        last_date = curve["date"].iloc[-1]
        span_seconds = max((last_date - first_date).total_seconds(), 1.0)

        def x_position(value: pd.Timestamp) -> int:
            elapsed = (value - first_date).total_seconds()
            return int(round(left + elapsed / span_seconds * (right - left)))

        points = [
            (x_position(pd.Timestamp(row.date)), y_position(float(value)))
            for row, value in zip(curve.itertuples(index=False), equity_pct)
        ]
        if len(points) > 1:
            draw.line(points, fill="#136f63", width=3, joint="curve")
        elif points:
            x, y = points[0]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#136f63")
        for tick in tick_dates:
            x = x_position(pd.Timestamp(tick))
            draw.line((x, bottom, x, bottom + 6), fill="#625b4f", width=1)
            draw.text(
                (x, bottom + 10),
                pd.Timestamp(tick).strftime("%Y-%m-%d"),
                fill="#625b4f",
                font=font,
                anchor="ma",
            )

    draw.line((left, top, left, bottom), fill="#332f2a", width=2)
    draw.line((left, bottom, right, bottom), fill="#332f2a", width=2)
    draw.text(
        ((left + right) // 2, 28),
        "Daily Equity (% of Initial)",
        fill="#332f2a",
        font=font,
        anchor="mm",
    )
    draw.text(
        ((left + right) // 2, height - 22),
        "Date",
        fill="#332f2a",
        font=font,
        anchor="mm",
    )
    image.save(output, format="PNG")
    return output


def publish_backtest_report(
    output_dir: str | Path,
    *,
    artifacts: ReplayArtifacts,
    initial_equity: float,
    metadata_gaps: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    reproduction_command: Mapping[str, Any],
    daily_market_bars: pd.DataFrame | None = None,
    entry_trade_dates: pd.DataFrame | None = None,
    symbols: Sequence[str] = (),
    assumed_mechanics: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], Path]:
    """Create one non-overwriting report directory and return its summary."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    reported_trades = _with_market_liquidity(
        artifacts.trades,
        daily_market_bars=(
            daily_market_bars if daily_market_bars is not None else pd.DataFrame()
        ),
        entry_trade_dates=(
            entry_trade_dates if entry_trade_dates is not None else pd.DataFrame()
        ),
    )
    performance: dict[str, Any] | None = None
    primary_curve: list[dict[str, Any]] | None = None
    if not metadata_gaps and not artifacts.daily_equity.empty:
        performance = compute_performance_metrics(
            artifacts.trades,
            artifacts.daily_equity,
            initial_equity=initial_equity,
        )
        performance["average_win_loss_ratio"] = _average_win_loss_ratio(
            artifacts.trades
        )
        primary_curve = artifacts.daily_equity.to_dict("records")
    funnel = build_funnel(
        candidates=artifacts.candidates,
        plans=artifacts.plans,
        orders=artifacts.orders,
        fills=artifacts.fills,
        trades=artifacts.trades,
    )
    summary = build_official_summary(
        funnel=funnel,
        metadata_gaps=[dict(row) for row in metadata_gaps],
        performance=performance,
        primary_curve=primary_curve,
        assumed_mechanics=assumed_mechanics,
    )
    summary.update(dict(context))
    summary["initial_equity"] = float(initial_equity)
    summary["reproduction_command"] = dict(reproduction_command)
    summary["portfolio_risk"] = _portfolio_risk_summary(artifacts)
    summary["position_scaling"] = _position_scaling_summary(
        artifacts,
        summary.get("position_scaling_config", {}),
    )

    group_report = (
        build_group_report(artifacts.trades)
        if not artifacts.trades.empty
        else _empty_group_report()
    )
    symbol_performance = _build_symbol_performance(
        artifacts.trades,
        symbols=symbols,
        initial_equity=initial_equity,
    )
    tables = {
        "candidates.csv": artifacts.candidates,
        "plans.csv": artifacts.plans,
        "orders.csv": artifacts.orders,
        "fills.csv": artifacts.fills,
        "exit_legs.csv": artifacts.exit_legs,
        "trades.csv": reported_trades,
        "daily_equity.csv": artifacts.daily_equity,
        "performance_by_group.csv": group_report,
        "performance_by_symbol.csv": symbol_performance,
        "rejections.csv": artifacts.rejections,
        "position_scaling_events.csv": artifacts.position_scaling_events,
        "fee_audit.csv": artifacts.exit_legs.reindex(columns=FEE_AUDIT_COLUMNS),
        "metadata_assumptions.csv": pd.DataFrame(
            [dict(item) for item in assumed_mechanics],
            columns=[
                "root_symbol",
                "contract_code",
                "exchange_trade_date",
                "field",
                "reason_code",
                "fallback",
            ],
        ),
    }
    for filename, frame in tables.items():
        _safe_csv(frame).to_csv(target / filename, index=False)
    _render_daily_equity_chart(
        target / "daily_equity_curve.png",
        daily_equity=artifacts.daily_equity,
        initial_equity=initial_equity,
    )
    (target / "summary.json").write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2, default=str)
        + "\n",
        encoding="utf-8",
    )
    shell_command = str(reproduction_command.get("shell_command", "")).strip()
    if not shell_command:
        raise ValueError("reproduction shell_command is required")
    command_path = target / "RUN_COMMAND.sh"
    command_path.write_text(shell_command + "\n", encoding="utf-8")
    command_path.chmod(0o755)
    (target / "report.md").write_text(
        _render_report(summary),
        encoding="utf-8",
    )
    return summary, target


def _average_win_loss_ratio(trades: pd.DataFrame) -> float:
    if "net_pnl" not in trades or trades.empty:
        return math.nan
    pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").dropna()
    wins = pnl.loc[pnl > 0]
    losses = pnl.loc[pnl < 0]
    if wins.empty or losses.empty:
        return math.nan
    return float(wins.mean() / abs(losses.mean()))


def _portfolio_risk_summary(artifacts: ReplayArtifacts) -> dict[str, Any]:
    daily = artifacts.daily_equity

    def maximum(column: str, fallback: str | None = None) -> float:
        selected = column if column in daily else fallback
        if selected is None or selected not in daily or daily.empty:
            return 0.0
        values = pd.to_numeric(daily[selected], errors="coerce").dropna()
        return float(values.max()) if not values.empty else 0.0

    rejection_counts: dict[str, int] = {}
    if "reason_code" in artifacts.rejections:
        reasons = artifacts.rejections["reason_code"].astype(str).str.strip()
        counts = reasons.loc[reasons.ne("")].value_counts().sort_index()
        rejection_counts = {
            str(reason): int(count) for reason, count in counts.items()
        }
    return {
        "peak_intraday_margin_utilization": maximum(
            "peak_margin_utilization", "margin_utilization"
        ),
        "peak_overnight_margin_utilization": maximum(
            "peak_overnight_margin_utilization"
        ),
        "peak_symbol_margin_utilization": maximum(
            "peak_symbol_margin_utilization", "margin_utilization"
        ),
        "max_concurrent_positions": int(maximum("max_concurrent_positions")),
        "rejection_counts": rejection_counts,
        "overnight_margin_breach_events": rejection_counts.get(
            "OVERNIGHT_MARGIN_LIMIT_BREACH", 0
        ),
        "overnight_reduction_limit_locked_events": rejection_counts.get(
            "OVERNIGHT_REDUCTION_LIMIT_LOCKED", 0
        ),
    }


def _position_scaling_summary(
    artifacts: ReplayArtifacts,
    config: object,
) -> dict[str, Any]:
    effective = dict(config) if isinstance(config, Mapping) else {}
    events = artifacts.position_scaling_events

    def event_count(scope: str, event_type: str) -> int:
        if not {"scope", "event_type"}.issubset(events) or events.empty:
            return 0
        return int(
            (
                events["scope"].astype(str).eq(scope)
                & events["event_type"].astype(str).eq(event_type)
            ).sum()
        )

    scaled_entry_count = 0
    if "quantity_scale" in artifacts.trades and not artifacts.trades.empty:
        scales = pd.to_numeric(
            artifacts.trades["quantity_scale"], errors="coerce"
        )
        scaled_entry_count = int(scales.lt(1.0).sum())
    return {
        "symbol_loss_streak": effective.get("symbol_loss_streak"),
        "symbol_position_scale": effective.get("symbol_position_scale"),
        "portfolio_drawdown_threshold": effective.get(
            "portfolio_drawdown_threshold"
        ),
        "portfolio_position_scale": effective.get("portfolio_position_scale"),
        "symbol_trigger_count": event_count("SYMBOL", "TRIGGER"),
        "portfolio_trigger_count": event_count("PORTFOLIO", "TRIGGER"),
        "symbol_recovery_count": event_count("SYMBOL", "RECOVER"),
        "portfolio_recovery_count": event_count("PORTFOLIO", "RECOVER"),
        "scaled_entry_count": scaled_entry_count,
    }


def _empty_group_report() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "sector",
            "setup",
            "cycle",
            "direction",
            "trade_count",
            "win_rate",
            "net_pnl",
            "average_win",
            "average_loss",
            "expectancy_R",
            "fees",
            "slippage",
            "average_mfe_R",
            "average_mae_R",
            "average_holding_bars",
            "profit_factor",
        ]
    )


def _render_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# 大周期定方向、小周期入场趋势策略回测",
        "",
        f"- 正式状态：`{summary.get('requested_interval_status', 'UNKNOWN')}`",
        f"- 回测区间：`{summary.get('requested_start', '')}` 至 `{summary.get('requested_end', '')}`",
        f"- 品种：`{', '.join(str(value) for value in summary.get('requested_symbols', ()))}`",
        f"- 初始权益：`{float(summary.get('initial_equity', 0.0)):,.2f}`",
        "",
        "## 运行命令",
        "",
        "```bash",
        str(summary.get("reproduction_command", {}).get("shell_command", "")),
        "```",
        "",
        "## 回测结果",
        "",
    ]
    performance = summary.get("official_performance")
    scenario_performance = summary.get("scenario_performance")
    if not isinstance(performance, Mapping) and isinstance(
        scenario_performance, Mapping
    ):
        lines.append(
            "以下仅为 `NON_CAUSAL_SCENARIO` 情景绩效，不属于正式 OOS 结果。"
        )
        performance = scenario_performance
    if isinstance(performance, Mapping):
        fields = (
            ("交易次数", "trade_count", "count"),
            ("净收益", "total_net_pnl", "cash"),
            ("总收益率", "total_return", "percent"),
            ("年化收益率", "annualized_return", "percent"),
            ("最大回撤", "max_drawdown", "percent"),
            ("胜率", "win_rate", "percent"),
            ("利润因子", "profit_factor", "ratio"),
            ("平均盈亏比", "average_win_loss_ratio", "ratio"),
            ("每笔期望 R", "expectancy_R", "ratio"),
            ("手续费", "fees", "cash"),
            ("滑点成本", "slippage", "cash"),
            ("期末权益", "final_equity", "cash"),
        )
        lines.extend(
            f"- {label}：{_format_value(performance.get(key), kind)}"
            for label, key, kind in fields
        )
    else:
        lines.append("正式绩效为空；请查看元数据缺口，不能用假设机制替代该区间结果。")
    lines.extend(["", "## 漏斗", ""])
    lines.extend(
        f"- `{name}`：{value}"
        for name, value in summary.get("funnel", {}).items()
    )
    gaps = summary.get("metadata_gaps", ())
    if gaps:
        lines.extend(["", "## 元数据缺口", ""])
        lines.extend(
            f"- `{row.get('root_symbol', 'UNKNOWN')}` "
            f"`{row.get('reason_code', 'BLOCKED_METADATA')}`："
            f"{row.get('reason', row.get('detail', ''))}"
            for row in gaps
        )
    assumptions = summary.get("assumed_mechanics", ())
    if assumptions:
        lines.extend(["", "## 元数据默认回退", ""])
        lines.extend(
            f"- `{row.get('root_symbol', 'UNKNOWN')}` "
            f"`{row.get('contract_code', '')}` "
            f"`{row.get('exchange_trade_date', '')}`："
            f"{row.get('field', '')} -> {row.get('fallback', '')}"
            for row in assumptions
        )
    fail_open = summary.get("gate_fail_open", {})
    evaluations = summary.get("gate_evaluations", {})
    warnings = []
    if isinstance(fail_open, Mapping) and isinstance(evaluations, Mapping):
        for gate, count in sorted(fail_open.items()):
            total = int(evaluations.get(gate, 0) or 0)
            failures = int(count or 0)
            rate = failures / total if total > 0 else 0.0
            if rate > 0.95:
                warnings.append((str(gate), failures, total, rate))
    if warnings:
        lines.extend(["", "## 闸门降级警告", ""])
        lines.extend(
            f"- `{gate}`：fail-open {failures}/{total}（{rate:.2%}），疑似未生效"
            for gate, failures, total, rate in warnings
        )
    portfolio = summary.get("portfolio_risk", {})
    if isinstance(portfolio, Mapping):
        lines.extend(
            [
                "",
                "## 组合风控",
                "",
                "- 日内保证金峰值："
                + _format_value(
                    portfolio.get("peak_intraday_margin_utilization"), "percent"
                ),
                "- 隔夜保证金峰值："
                + _format_value(
                    portfolio.get("peak_overnight_margin_utilization"), "percent"
                ),
                "- 单品种保证金峰值："
                + _format_value(
                    portfolio.get("peak_symbol_margin_utilization"), "percent"
                ),
                "- 最大同时持仓数："
                + _format_value(
                    portfolio.get("max_concurrent_positions"), "count"
                ),
                "- 隔夜保证金超限事件："
                + _format_value(
                    portfolio.get("overnight_margin_breach_events"), "count"
                ),
                "- 隔夜减仓锁板事件："
                + _format_value(
                    portfolio.get("overnight_reduction_limit_locked_events"),
                    "count",
                ),
            ]
        )
        rejection_counts = portfolio.get("rejection_counts", {})
        if isinstance(rejection_counts, Mapping) and rejection_counts:
            lines.extend(
                f"- 拒绝 `{reason}`：{int(count)}"
                for reason, count in rejection_counts.items()
            )
    scaling = summary.get("position_scaling", {})
    if isinstance(scaling, Mapping):
        lines.extend(
            [
                "",
                "## 动态仓位",
                "",
                "- 品种连续亏损触发笔数："
                + _format_value(scaling.get("symbol_loss_streak"), "count"),
                "- 品种缩放系数："
                + _format_value(scaling.get("symbol_position_scale"), "ratio"),
                "- 组合回撤触发阈值："
                + _format_value(
                    scaling.get("portfolio_drawdown_threshold"), "percent"
                ),
                "- 组合缩放系数："
                + _format_value(
                    scaling.get("portfolio_position_scale"), "ratio"
                ),
                "- 品种触发/恢复次数："
                + _format_value(scaling.get("symbol_trigger_count"), "count")
                + "/"
                + _format_value(scaling.get("symbol_recovery_count"), "count"),
                "- 组合触发/恢复次数："
                + _format_value(
                    scaling.get("portfolio_trigger_count"), "count"
                )
                + "/"
                + _format_value(
                    scaling.get("portfolio_recovery_count"), "count"
                ),
                "- 使用缩放仓位的成交笔数："
                + _format_value(scaling.get("scaled_entry_count"), "count"),
            ]
        )
    lines.extend(
        [
            "",
            "## 审计文件",
            "",
            "候选、计划、订单、成交、完整交易、逐日权益、分组绩效、动态仓位事件和拒绝原因均保存在同目录 CSV 中。",
            "每次机会的图表及索引保存在 `opportunity_charts/`。",
            "",
        ]
    )
    return "\n".join(lines)


def _format_value(value: object, kind: str) -> str:
    if value is None:
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(number):
        return "N/A"
    if kind == "count":
        return str(int(number))
    if kind == "cash":
        return f"{number:,.2f}"
    if kind == "percent":
        return f"{number:.2%}"
    return f"{number:.4f}"


def _safe_csv(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.copy().replace([np.inf, -np.inf], np.nan)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


__all__ = ["publish_backtest_report"]
