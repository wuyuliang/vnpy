"""Build live periodic/review reports from CSV exports."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cta.live.reporting.periodic_report import write_periodic_reports
from cta.live.reporting.review_report import write_review_report


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build live periodic/review reports from trade CSV.")
    p.add_argument("--live-trades-csv", required=True, help="live trade details CSV path")
    p.add_argument("--out-dir", required=True, help="output directory")
    p.add_argument("--oot-trades-csv", default="", help="optional oot trade details CSV path")
    p.add_argument("--run-tag", default="live", help="output file prefix tag")
    p.add_argument("--initial-capital", type=float, default=10_000_000.0)
    p.add_argument("--risk-free-annual-return", type=float, default=0.02)
    p.add_argument("--benchmark-annual-return", type=float, default=0.02)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    live_df = pd.read_csv(args.live_trades_csv, encoding="utf-8-sig")
    periodic_paths = write_periodic_reports(
        trade_details=live_df,
        out_dir=out_dir,
        run_tag=args.run_tag,
        initial_capital=float(args.initial_capital),
        risk_free_annual_return=float(args.risk_free_annual_return),
        benchmark_annual_return=float(args.benchmark_annual_return),
    )
    print(f"[periodic] monthly={periodic_paths['monthly']}")
    print(f"[periodic] weekly={periodic_paths['weekly']}")
    print(f"[periodic] summary={periodic_paths['summary']}")

    oot_csv = str(args.oot_trades_csv or "").strip()
    if oot_csv:
        oot_df = pd.read_csv(oot_csv, encoding="utf-8-sig")
        review_paths = write_review_report(
            live_trade_details=live_df,
            oot_trade_details=oot_df,
            out_dir=out_dir,
            run_tag=args.run_tag,
        )
        print(f"[review] details={review_paths['details']}")
        print(f"[review] summary={review_paths['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

