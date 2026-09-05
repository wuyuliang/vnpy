from __future__ import annotations

import pandas as pd

from cta.strategy.second_leg_down.backtest.runner import (
    DEFAULT_OUTPUT_ROOT,
    _capital_basis_summary,
    build_parser,
    build_reproduction_command,
)


def test_runner_defaults_are_specific_to_second_leg_down() -> None:
    args = build_parser().parse_args(["--start", "2026-01-01", "--end", "2026-02-01"])

    assert args.risk_per_trade == 0.005
    assert args.output_root == str(DEFAULT_OUTPUT_ROOT)


def test_reproduction_command_uses_the_second_leg_module() -> None:
    args = build_parser().parse_args(
        ["--start", "2026-01-01", "--end", "2026-02-01", "--symbols", "RB"]
    )

    command = build_reproduction_command(args)

    assert command["argv"][:3] == [
        "python3",
        "-m",
        "cta.strategy.second_leg_down.backtest.runner",
    ]


def test_capital_basis_summary_counts_candidates_and_quantity() -> None:
    candidates = pd.DataFrame(
        {
            "capital_basis": ["margin", "notional", "margin"],
            "quantity": [3, 1, 0],
            "filtered_reason": ["", "", "CAPITAL_LIMIT"],
        }
    )

    assert _capital_basis_summary(candidates) == {
        "margin": {"candidate_count": 2, "eligible_count": 1, "quantity": 3},
        "notional": {"candidate_count": 1, "eligible_count": 1, "quantity": 1},
    }
