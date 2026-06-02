"""Daily update orchestrator: download -> integrity gate -> features -> model features."""
from __future__ import annotations

import argparse
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_DOWNLOAD_INTERVAL_MAP: dict[str, str] = {
    "day": "day",
    "d": "day",
    "60min": "minute60",
    "minute60": "minute60",
    "30min": "minute30",
    "minute30": "minute30",
    "15min": "minute15",
    "minute15": "minute15",
    "5min": "minute5",
    "minute5": "minute5",
    "min": "minute",
    "minute": "minute",
}

_MODEL_INTERVAL_MAP: dict[str, str] = {
    "day": "day",
    "minute60": "60min",
    "minute30": "30min",
    "minute15": "15min",
    "minute5": "5min",
    "minute": "min",
}


def _norm_download_interval(itv: str) -> str:
    key = str(itv).strip().lower()
    if key not in _DOWNLOAD_INTERVAL_MAP:
        raise ValueError(f"unsupported interval token: {itv!r}")
    return _DOWNLOAD_INTERVAL_MAP[key]


def _norm_model_interval(itv: str) -> str:
    return _MODEL_INTERVAL_MAP.get(_norm_download_interval(itv), str(itv).strip())


@dataclass(frozen=True)
class DailyUpdateConfig:
    """Config for one daily update run."""

    top_n_symbols: int = 18
    symbols_ranking_path: Path = Path("cta/feature/symbols_research_ranking.csv")
    intervals: tuple[str, ...] = ("day", "60min", "30min")
    start_date: str = "2010-01-01"
    end_date: str = "2025-12-31"
    run_tag: str | None = None
    workers: int = 4
    rate_limit: int = 450
    include_financial: bool = True
    include_index: bool = True
    build_macro: bool = True
    enable_integrity_gate: bool = True
    enable_feature_generation: bool = True
    enable_candidate_dataset: bool = True
    candidate_trade_side_mode: str = "both"
    candidate_horizon_bars: int = 20
    origin_root: Path = Path("cta/data/origin")


def build_commands(cfg: DailyUpdateConfig) -> list[list[str]]:
    dl_intervals = [_norm_download_interval(itv) for itv in cfg.intervals]
    model_intervals = [_norm_model_interval(itv) for itv in cfg.intervals]
    cmds: list[list[str]] = []

    dl_cmd = [
        "python3",
        "-m",
        "cta.data_code.download_all",
        "--intervals",
        *dl_intervals,
        "--start",
        cfg.start_date,
        "--end",
        cfg.end_date,
        "--max-rank",
        str(int(cfg.top_n_symbols)),
        "--workers",
        str(int(cfg.workers)),
        "--rate-limit",
        str(int(cfg.rate_limit)),
    ]
    if cfg.include_financial:
        dl_cmd.append("--include-financial")
    else:
        dl_cmd.append("--exclude-financial")
    if cfg.include_index:
        dl_cmd.append("--include-index")
    else:
        dl_cmd.append("--exclude-index")
    if cfg.build_macro:
        dl_cmd.append("--build-macro")
    else:
        dl_cmd.append("--no-build-macro")
    cmds.append(dl_cmd)

    if cfg.enable_integrity_gate:
        cmds.append(
            [
                "python3",
                "-m",
                "cta.data_code.data_integrity_check",
                "--origin-root",
                str(cfg.origin_root),
                "--intervals",
                *cfg.intervals,
            ]
        )

    if cfg.enable_feature_generation:
        feat_cmd = [
            "python3",
            "-m",
            "cta.feature.run_all_features",
            "--interval",
            *dl_intervals,
            "--max-rank",
            str(int(cfg.top_n_symbols)),
            "--workers",
            str(int(cfg.workers)),
        ]
        if cfg.build_macro:
            feat_cmd.append("--build-macro")
        cmds.append(feat_cmd)

    if cfg.enable_candidate_dataset:
        ccmd = [
            "python3",
            "-m",
            "cta.model.feature.candidate_training_dataset",
            "--top-n-symbols",
            str(int(cfg.top_n_symbols)),
            "--symbols-ranking-path",
            str(cfg.symbols_ranking_path),
            "--interval",
            *model_intervals,
            "--start",
            cfg.start_date,
            "--end",
            cfg.end_date,
            "--trade-side-mode",
            cfg.candidate_trade_side_mode,
            "--horizon-bars",
            str(int(cfg.candidate_horizon_bars)),
        ]
        if cfg.run_tag:
            ccmd.extend(["--run-tag", str(cfg.run_tag)])
        cmds.append(ccmd)
    return cmds


def _default_runner(cmd: list[str]) -> int:
    proc = subprocess.run(cmd, check=False)  # noqa: S603
    return int(proc.returncode)


def run_daily_update(
    cfg: DailyUpdateConfig,
    *,
    command_runner: Callable[[list[str]], int] | None = None,
) -> list[list[str]]:
    runner = command_runner or _default_runner
    commands = build_commands(cfg)
    executed: list[list[str]] = []
    for idx, cmd in enumerate(commands, start=1):
        logger.info("[daily_update] step %d/%d: %s", idx, len(commands), " ".join(cmd))
        rc = int(runner(cmd))
        executed.append(cmd)
        if rc != 0:
            raise RuntimeError(f"daily_update step failed rc={rc}: {' '.join(cmd)}")
    return executed


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Daily update pipeline for CTA origin/feature/model_feature")
    p.add_argument("--top-n-symbols", type=int, default=18)
    p.add_argument("--symbols-ranking-path", default="cta/feature/symbols_research_ranking.csv")
    p.add_argument("--intervals", nargs="+", default=["day", "60min", "30min"])
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--run-tag", default=None)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--rate-limit", type=int, default=450)
    p.add_argument("--exclude-financial", action="store_true")
    p.add_argument("--exclude-index", action="store_true")
    p.add_argument("--no-build-macro", action="store_true")
    p.add_argument("--disable-integrity-gate", action="store_true")
    p.add_argument("--disable-feature-generation", action="store_true")
    p.add_argument("--disable-candidate-dataset", action="store_true")
    p.add_argument("--candidate-trade-side-mode", default="both")
    p.add_argument("--candidate-horizon-bars", type=int, default=20)
    p.add_argument("--origin-root", default="cta/data/origin")
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _build_parser().parse_args()
    cfg = DailyUpdateConfig(
        top_n_symbols=int(args.top_n_symbols),
        symbols_ranking_path=Path(args.symbols_ranking_path),
        intervals=tuple(args.intervals),
        start_date=str(args.start),
        end_date=str(args.end),
        run_tag=args.run_tag,
        workers=int(args.workers),
        rate_limit=int(args.rate_limit),
        include_financial=not bool(args.exclude_financial),
        include_index=not bool(args.exclude_index),
        build_macro=not bool(args.no_build_macro),
        enable_integrity_gate=not bool(args.disable_integrity_gate),
        enable_feature_generation=not bool(args.disable_feature_generation),
        enable_candidate_dataset=not bool(args.disable_candidate_dataset),
        candidate_trade_side_mode=str(args.candidate_trade_side_mode),
        candidate_horizon_bars=int(args.candidate_horizon_bars),
        origin_root=Path(args.origin_root),
    )
    run_daily_update(cfg)


if __name__ == "__main__":
    main()


__all__ = ["DailyUpdateConfig", "build_commands", "run_daily_update"]
