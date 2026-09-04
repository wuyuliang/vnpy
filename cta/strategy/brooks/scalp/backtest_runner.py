"""Frozen RB/CU rule-only scalp backtest and atomic report CLI."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .config import DEFAULT_CONFIG_PATH, ScalpConfig, config_sha256, load_config
from .metadata import BlockedMetadataError, DEFAULT_META_ROOT, MetadataBundle
from .metadata_importer import validate_requested_coverage
from .metrics import build_group_metrics, compute_trade_metrics, equity_metrics
from .report import (
    AtomicReportPublisher,
    plot_portfolio_equity,
    plot_symbol_timeframes,
    render_report_markdown,
    write_json,
)


REQUIRED_OUTPUT_FILES = {
    "source_audit.json",
    "config_snapshot.yaml",
    "contract_mapping.csv",
    "daily_trading_specs.csv",
    "candidates.csv",
    "rejections.csv",
    "risk_decisions.csv",
    "orders.csv",
    "order_events.csv",
    "fills.csv",
    "trades.csv",
    "minute_equity.parquet",
    "daily_equity.csv",
    "daily_metrics.csv",
    "monthly_metrics.csv",
    "group_metrics.csv",
    "stress_results.csv",
    "risk_score.json",
    "summary.json",
    "report.md",
    "charts/RB_30m_5m_1m_trades.png",
    "charts/CU_30m_5m_1m_trades.png",
    "charts/portfolio_equity_drawdown.png",
}


@dataclass
class BacktestArtifacts:
    status: str
    source_audit: dict[str, Any]
    contract_mapping: pd.DataFrame
    daily_trading_specs: pd.DataFrame
    candidates: pd.DataFrame
    rejections: pd.DataFrame
    risk_decisions: pd.DataFrame
    orders: pd.DataFrame
    order_events: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    minute_equity: pd.DataFrame
    daily_equity: pd.DataFrame
    stress_results: pd.DataFrame
    risk_score: dict[str, Any]
    summary: dict[str, Any]
    symbol_bars: dict[str, pd.DataFrame]


def publish_backtest_artifacts(
    artifacts: BacktestArtifacts,
    output_dir: str | Path,
    *,
    config: ScalpConfig,
) -> Path:
    """Derive metrics independently, then atomically publish the fixed artifact set."""
    target = Path(output_dir)
    publisher = AtomicReportPublisher(target, required_files=REQUIRED_OUTPUT_FILES)
    trade_metrics = compute_trade_metrics(artifacts.trades)
    portfolio_metrics = equity_metrics(
        artifacts.daily_equity,
        initial_equity=config.account.initial_equity,
    )
    group_metrics = build_group_metrics(
        artifacts.trades,
        minimum_trades=config.validation.minimum_group_trades,
        minimum_win_rate=config.validation.minimum_net_win_rate,
        payoff_min=config.trade_plan.allowed_net_payoff_min,
        payoff_max=config.trade_plan.allowed_net_payoff_max,
        required_symbols=tuple(artifacts.source_audit.get("symbols", ())),
        initial_equity=config.account.initial_equity,
        daily_dates=artifacts.daily_equity.get("date"),
    )
    daily_metrics, monthly_metrics = _period_metrics(
        artifacts.daily_equity,
        initial_equity=config.account.initial_equity,
    )
    summary = {
        **artifacts.summary,
        "status": artifacts.status,
        "strategy_version": f"scalp-{config_sha256(config)[:12]}",
        "config_sha256": config_sha256(config),
        "trade_metrics": _json_safe(trade_metrics),
        "portfolio_metrics": _json_safe(portfolio_metrics),
        "daily_target_passed": (
            portfolio_metrics["average_daily_return"]
            >= config.validation.average_daily_return_target
        ),
        "monthly_target_passed": (
            portfolio_metrics["average_monthly_return"]
            >= config.validation.average_monthly_return_target
        ),
    }
    failures = _failures(summary, group_metrics, artifacts.risk_score)
    report = render_report_markdown(
        status=artifacts.status,
        failures=failures,
        source_audit=artifacts.source_audit,
        symbol_summaries=_symbol_summaries(artifacts.trades, artifacts.summary),
        portfolio_summary=summary,
        risk_score=artifacts.risk_score,
        stress_results=artifacts.stress_results.to_dict("records"),
        limitations=[
            "日均 1% 与月均 20% 是研究目标，不是收益承诺。",
            "任一冻结分组样本少于 100 笔时结论保持 INCONCLUSIVE。",
            "当前费用和保证金是 SHFE 交易所标准，未含经纪商加收，不能用于实盘准入。",
        ],
    )
    with publisher.staging_directory() as staging:
        write_json(staging / "source_audit.json", _json_safe(artifacts.source_audit))
        (staging / "config_snapshot.yaml").write_text(
            yaml.safe_dump(config.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tables = {
            "contract_mapping.csv": artifacts.contract_mapping,
            "daily_trading_specs.csv": artifacts.daily_trading_specs,
            "candidates.csv": artifacts.candidates,
            "rejections.csv": artifacts.rejections,
            "risk_decisions.csv": artifacts.risk_decisions,
            "orders.csv": artifacts.orders,
            "order_events.csv": artifacts.order_events,
            "fills.csv": artifacts.fills,
            "trades.csv": artifacts.trades,
            "daily_equity.csv": artifacts.daily_equity,
            "daily_metrics.csv": daily_metrics,
            "monthly_metrics.csv": monthly_metrics,
            "group_metrics.csv": group_metrics,
            "stress_results.csv": artifacts.stress_results,
        }
        for filename, frame in tables.items():
            _write_csv(staging / filename, frame)
        artifacts.minute_equity.to_parquet(staging / "minute_equity.parquet", index=False)
        write_json(staging / "risk_score.json", _json_safe(artifacts.risk_score))
        write_json(staging / "summary.json", _json_safe(summary))
        (staging / "report.md").write_text(report, encoding="utf-8")
        for root in ("RB", "CU"):
            bars = artifacts.symbol_bars.get(root, pd.DataFrame())
            symbol_fills = _symbol_rows(artifacts.fills, root)
            plot_symbol_timeframes(
                bars,
                symbol_fills,
                staging / f"charts/{root}_30m_5m_1m_trades.png",
                title=f"{root} 30m / 5m / 1m trades",
            )
        plot_portfolio_equity(
            artifacts.daily_equity,
            staging / "charts/portfolio_equity_drawdown.png",
        )
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["RB0.SHFE", "CU0.SHFE"])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--meta-root", default=str(DEFAULT_META_ROOT))
    parser.add_argument("--data-root", default="cta/data/origin/minute")
    parser.add_argument("--output-root", default="cta/strategy/brooks/report/scalp")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--research-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        validate_requested_coverage(
            args.meta_root,
            symbols=args.symbols,
            start=args.start,
            end=args.end,
        )
        metadata = MetadataBundle.load(args.meta_root)
    except (BlockedMetadataError, FileNotFoundError, ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED_METADATA", "reason": str(exc)}, ensure_ascii=False))
        return 2
    try:
        artifacts = run_frozen_backtest(
            symbols=tuple(args.symbols),
            start=args.start,
            end=args.end,
            data_root=Path(args.data_root),
            metadata=metadata,
            config=config,
            research_only=bool(args.research_only),
        )
        run_id = args.run_id or _default_run_id(args.start, args.end, config)
        output = publish_backtest_artifacts(
            artifacts,
            Path(args.output_root) / run_id,
            config=config,
        )
    except (BlockedMetadataError, ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED_METADATA", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": artifacts.status, "output": str(output)}, ensure_ascii=False))
    return 0


def run_frozen_backtest(
    *,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    data_root: Path,
    metadata: MetadataBundle,
    config: ScalpConfig,
    research_only: bool,
) -> BacktestArtifacts:
    """Load exact-contract bars and delegate to the causal research pipeline."""
    from .research_pipeline import run_research_pipeline

    return run_research_pipeline(
        symbols=symbols,
        start=start,
        end=end,
        data_root=data_root,
        metadata=metadata,
        config=config,
        research_only=research_only,
    )


def _period_metrics(
    daily_equity: pd.DataFrame,
    *,
    initial_equity: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if daily_equity.empty:
        return (
            pd.DataFrame(columns=["date", "equity", "return"]),
            pd.DataFrame(columns=["month", "equity", "return"]),
        )
    daily = daily_equity.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date")
    daily_equity_values = pd.to_numeric(daily["equity"])
    daily["return"] = daily_equity_values.pct_change()
    daily.loc[daily.index[0], "return"] = (
        float(daily_equity_values.iloc[0]) / initial_equity - 1.0
    )
    monthly = daily.set_index("date")["equity"].resample("ME").last().reset_index()
    monthly = monthly.rename(columns={"date": "month"})
    monthly_equity = pd.to_numeric(monthly["equity"])
    monthly["return"] = monthly_equity.pct_change()
    monthly.loc[monthly.index[0], "return"] = (
        float(monthly_equity.iloc[0]) / initial_equity - 1.0
    )
    return daily, monthly


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    safe = frame.copy()
    safe = safe.replace([np.inf, -np.inf], np.nan)
    safe.to_csv(path, index=False)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _failures(
    summary: dict[str, Any],
    groups: pd.DataFrame,
    risk_score: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if not summary.get("daily_target_passed", False):
        failures.append("日均 1% 目标未通过。")
    if not summary.get("monthly_target_passed", False):
        failures.append("月均 20% 目标未通过。")
    if not groups.empty and (groups["status"] != "PASSED").any():
        failures.append("至少一个必需分组 FAILED 或 INCONCLUSIVE。")
    if risk_score.get("status") != "PASSED":
        failures.append("RiskScore 未通过。")
    return failures


def _symbol_summaries(
    trades: pd.DataFrame,
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    independent = summary.get("independent_symbol_results", {})
    for root in ("RB", "CU"):
        matched = next(
            (
                value
                for symbol, value in independent.items()
                if str(symbol).upper().startswith(root)
            ),
            None,
        )
        result[root] = _json_safe(
            matched
            if matched is not None
            else compute_trade_metrics(_symbol_rows(trades, root))
        )
    return result


def _symbol_rows(frame: pd.DataFrame, root: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    for column in ("symbol", "contract_code"):
        if column in frame:
            return frame.loc[frame[column].astype(str).str.upper().str.startswith(root)].copy()
    return frame.iloc[0:0].copy()


def _default_run_id(start: str, end: str, config: ScalpConfig) -> str:
    return f"{start.replace('-', '')}_{end.replace('-', '')}_{config_sha256(config)[:8]}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "BacktestArtifacts",
    "REQUIRED_OUTPUT_FILES",
    "build_parser",
    "main",
    "publish_backtest_artifacts",
    "run_frozen_backtest",
]
