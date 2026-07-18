from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from .experiments import write_experiment_report


def _named_run(value: str) -> tuple[str, Path]:
    run_id, separator, raw_path = value.partition("=")
    if not separator or not run_id.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("--run must use RUN_ID=OUTPUT_DIR")
    return run_id.strip(), Path(raw_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare ETF rotation backtest outputs"
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        type=_named_run,
        metavar="RUN_ID=OUTPUT_DIR",
    )
    parser.add_argument("--benchmark-csv", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_dirs: dict[str, Path] = {}
    for run_id, run_dir in args.run:
        if run_id in run_dirs:
            parser.error(f"duplicate run id: {run_id}")
        if not run_dir.is_dir():
            parser.error(f"run output directory does not exist: {run_dir}")
        run_dirs[run_id] = run_dir
    if not args.benchmark_csv.is_file():
        parser.error(f"benchmark csv does not exist: {args.benchmark_csv}")
    benchmark = pd.read_csv(args.benchmark_csv)
    required = {"datetime", "close"}
    missing = required - set(benchmark.columns)
    if missing:
        parser.error(f"benchmark csv missing columns: {sorted(missing)}")
    try:
        write_experiment_report(
            run_dirs,
            benchmark,
            args.output_dir,
            baseline_run_id=args.baseline,
            candidate_run_id=args.candidate,
        )
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
