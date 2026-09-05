from __future__ import annotations

from cta.strategy.second_leg_brooks.backtest.runner import (
    DEFAULT_OUTPUT_ROOT,
    build_parser,
    build_reproduction_command,
)


def test_runner_defaults_and_reproduction_command_are_strategy_specific() -> None:
    args = build_parser().parse_args(
        ["--start", "2026-01-01", "--end", "2026-02-01", "--symbols", "RB"]
    )

    command = build_reproduction_command(args)

    assert args.risk_per_trade == 0.005
    assert args.output_root == str(DEFAULT_OUTPUT_ROOT)
    assert command["argv"][:3] == [
        "python3",
        "-m",
        "cta.strategy.second_leg_brooks.backtest.runner",
    ]
