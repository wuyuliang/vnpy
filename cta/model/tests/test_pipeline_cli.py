from __future__ import annotations

from cta.model.orchestration.pipeline_cli import _parse_args


def test_pipeline_cli_parse_args_smoke() -> None:
    ns = _parse_args(["--symbol", "RB0", "--interval", "60min"])
    assert ns.symbol == "RB0"

