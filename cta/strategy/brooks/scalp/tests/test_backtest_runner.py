from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from cta.strategy.brooks.scalp.backtest_runner import (
    BacktestArtifacts,
    REQUIRED_OUTPUT_FILES,
    main,
    publish_backtest_artifacts,
)
from cta.strategy.brooks.scalp.config import load_config


def _artifacts() -> BacktestArtifacts:
    dates = pd.date_range("2026-01-05", periods=3, freq="D")
    daily = pd.DataFrame({"date": dates, "equity": [200_000.0, 200_100.0, 200_050.0]})
    minute = pd.DataFrame(
        {
            "datetime": pd.date_range(
                "2026-01-05 09:01", periods=3, freq="min", tz="Asia/Shanghai"
            ),
            "marked_equity": [200_000.0, 200_050.0, 200_100.0],
            "margin_usage": [0.0, 0.1, 0.0],
        }
    )
    empty = pd.DataFrame({"id": pd.Series(dtype=str)})
    bars = pd.DataFrame(
        {
            "bar_end": pd.date_range(
                "2026-01-05 09:01", periods=20, freq="min", tz="Asia/Shanghai"
            ),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
        }
    )
    return BacktestArtifacts(
        status="INCONCLUSIVE",
        source_audit={"fixture": True, "causal_audit_passed": True},
        contract_mapping=empty,
        daily_trading_specs=empty,
        candidates=empty,
        rejections=empty,
        risk_decisions=empty,
        orders=empty,
        order_events=empty,
        fills=empty,
        trades=pd.DataFrame(
            columns=[
                "trade_id",
                "symbol",
                "direction",
                "rule_id",
                "net_pnl",
                "net_r",
            ]
        ),
        minute_equity=minute,
        daily_equity=daily,
        stress_results=pd.DataFrame(
            [{"scenario": "2x_fee", "net_pnl": 0.0, "profit_factor": 0.0}]
        ),
        risk_score={"score": 0, "status": "INCONCLUSIVE", "vetoes": []},
        summary={"status": "INCONCLUSIVE", "fixture": True},
        symbol_bars={"RB": bars, "CU": bars},
    )


def test_missing_metadata_blocks_before_performance_report(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "reports"
    code = main(
        [
            "--symbols",
            "RB0.SHFE",
            "CU0.SHFE",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-10",
            "--meta-root",
            str(tmp_path / "missing-meta"),
            "--output-root",
            str(output),
        ]
    )
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BLOCKED_METADATA"
    assert not list(output.glob("*/summary.json"))


def test_fixture_artifacts_publish_all_fixed_outputs_atomically(tmp_path: Path) -> None:
    output = tmp_path / "fixture-run"
    publish_backtest_artifacts(
        _artifacts(),
        output,
        config=load_config(),
    )
    present = {
        str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()
    }
    assert REQUIRED_OUTPUT_FILES <= present
    assert json.loads((output / "summary.json").read_text(encoding="utf-8"))["status"] == (
        "INCONCLUSIVE"
    )
    assert (output / "report.md").read_text(encoding="utf-8").startswith(
        "# Brooks 纯价格行为"
    )
    assert (output / "charts/RB_30m_5m_1m_trades.png").read_bytes().startswith(b"\x89PNG")
