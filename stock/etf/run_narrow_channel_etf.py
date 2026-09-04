"""Run the 159915.SZ weekly narrow-channel quick validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from PIL import Image

from .narrow_channel_etf_chart import (
    render_narrow_channel_chart,
    save_chart_atomic,
)
from .narrow_channel_etf_strategy import (
    NarrowChannelConfig,
    aggregate_complete_weeks,
    build_narrow_channel_signals,
    classify_weekly_channels,
    prepare_etf_bars,
    run_narrow_channel_backtest,
)

ETF_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ETF_ROOT / "output/20260727_chuangyeban_regime_overlay"
DEFAULT_SOURCE_CSV = DEFAULT_SOURCE_ROOT / "signals.csv"
DEFAULT_SOURCE_AUDIT = DEFAULT_SOURCE_ROOT / "source_audit.json"
DEFAULT_STRATEGY_DOCUMENT = ETF_ROOT / "20260817_zaitongdao_etf.md"
DEFAULT_OUTPUT_DIR = ETF_ROOT / "output/20260817_zaitongdao_etf"
CHART_FILENAME = "159915.SZ_周线窄通道快速验证.png"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_source_audit(path: Path, config: NarrowChannelConfig) -> dict[str, Any]:
    audit = json.loads(path.read_text(encoding="utf-8"))
    if audit.get("symbol") != config.symbol:
        raise ValueError(f"source audit must describe {config.symbol}")
    if audit.get("price_adjustment_mode") != "point_in_time_adjusted":
        raise ValueError("source audit must use point_in_time_adjusted prices")
    return audit


def _ensure_output_available(output_dir: Path, overwrite: bool) -> None:
    expected = (
        output_dir / "signals.csv",
        output_dir / "trades.csv",
        output_dir / "summary.json",
        output_dir / "charts" / CHART_FILENAME,
    )
    existing = [path for path in expected if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            f"output already exists; pass --overwrite: {existing[0]}"
        )
    if output_dir.exists():
        expected_relative = {
            path.relative_to(output_dir) for path in expected
        }
        actual_relative = {
            path.relative_to(output_dir) for path in output_dir.rglob("*") if path.is_file()
        }
        unexpected = actual_relative - expected_relative
        if unexpected:
            raise FileExistsError(
                f"output contains unexpected file: {sorted(unexpected)[0]}"
            )


def _validate_staging(staging_dir: Path) -> None:
    expected = {
        Path("signals.csv"),
        Path("trades.csv"),
        Path("summary.json"),
        Path("charts") / CHART_FILENAME,
    }
    actual = {
        path.relative_to(staging_dir)
        for path in staging_dir.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise RuntimeError("staged narrow-channel artifacts are incomplete")
    json.loads((staging_dir / "summary.json").read_text(encoding="utf-8"))
    pd.read_csv(staging_dir / "signals.csv")
    pd.read_csv(staging_dir / "trades.csv")
    with Image.open(staging_dir / "charts" / CHART_FILENAME) as image:
        image.verify()


def _publish_staging(staging_dir: Path, output_dir: Path) -> None:
    backup_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.backup")
    had_output = output_dir.exists()
    if had_output:
        output_dir.replace(backup_dir)
    try:
        staging_dir.replace(output_dir)
    except Exception:
        if had_output:
            backup_dir.replace(output_dir)
        raise
    else:
        if had_output:
            shutil.rmtree(backup_dir)


def run_and_write(
    *,
    source_csv: Path,
    source_audit_json: Path,
    strategy_document: Path,
    output_dir: Path,
    config: NarrowChannelConfig | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run the frozen research backtest and write its four documented artifacts."""
    cfg = config or NarrowChannelConfig()
    for path in (source_csv, source_audit_json, strategy_document):
        if not path.is_file():
            raise FileNotFoundError(path)
    _ensure_output_available(output_dir, overwrite)
    source_audit = _load_source_audit(source_audit_json, cfg)
    bars = prepare_etf_bars(pd.read_csv(source_csv), cfg)
    weekly = classify_weekly_channels(aggregate_complete_weeks(bars), cfg)
    signals = build_narrow_channel_signals(bars, weekly, cfg)
    result = run_narrow_channel_backtest(signals, cfg)

    summary: dict[str, Any] = {
        **result.summary,
        "strategy": "weekly_narrow_channel_etf_quick_validation",
        "source_csv": str(source_csv),
        "source_audit_json": str(source_audit_json),
        "strategy_document": str(strategy_document),
        "output_dir": str(output_dir),
        "source_sha256": _sha256(source_csv),
        "source_audit_sha256": _sha256(source_audit_json),
        "strategy_document_sha256": _sha256(strategy_document),
        "source_price_adjustment_mode": source_audit["price_adjustment_mode"],
        "last_complete_week": (
            str(pd.Timestamp(weekly.iloc[-1]["datetime"]).date())
            if not weekly.empty
            else None
        ),
        "weekly_state_counts": {
            str(key): int(value)
            for key, value in weekly["weekly_state"].value_counts().sort_index().items()
        },
        "parameters": {
            "channel_weeks": cfg.channel_weeks,
            "confirmation_windows": cfg.confirmation_windows,
            "weekly_atr_period": cfg.weekly_atr_period,
            "daily_atr_period": cfg.daily_atr_period,
            "daily_ema_periods": [5, 10, 20],
            "max_entry_atr": 0.75,
            "minimum_stop_atr": 1.50,
            "trailing_activation_r": 2.0,
            "one_way_cost_rate": cfg.one_way_cost_rate,
            "short_borrow_rate": cfg.short_borrow_rate,
        },
        "output_files": {
            "signals": "signals.csv",
            "trades": "trades.csv",
            "summary": "summary.json",
            "chart": f"charts/{CHART_FILENAME}",
        },
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.staging")
    try:
        staging_dir.mkdir()
        _atomic_write_csv(result.signals, staging_dir / "signals.csv")
        _atomic_write_csv(result.trades, staging_dir / "trades.csv")
        _atomic_write_json(summary, staging_dir / "summary.json")
        image = render_narrow_channel_chart(
            signals=result.signals,
            weekly=weekly,
            trades=result.trades,
            summary=summary,
        )
        save_chart_atomic(image, staging_dir / "charts" / CHART_FILENAME)
        _validate_staging(staging_dir)
        _publish_staging(staging_dir, output_dir)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    return summary


def build_parser() -> argparse.ArgumentParser:
    """Build the local, single-symbol backtest CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, default=DEFAULT_SOURCE_CSV)
    parser.add_argument(
        "--source-audit-json",
        type=Path,
        default=DEFAULT_SOURCE_AUDIT,
    )
    parser.add_argument(
        "--strategy-document",
        type=Path,
        default=DEFAULT_STRATEGY_DOCUMENT,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the quick validation and print its JSON summary."""
    args = build_parser().parse_args(argv)
    summary = run_and_write(
        source_csv=args.source_csv,
        source_audit_json=args.source_audit_json,
        strategy_document=args.strategy_document,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
