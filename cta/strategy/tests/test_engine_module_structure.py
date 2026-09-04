from __future__ import annotations

import ast
import importlib
from pathlib import Path


def test_backtest_engine_responsibilities_are_split() -> None:
    path = Path(
        "cta/strategy/multi_timeframe_trend_backtest/engine.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    ]

    assert len(source.splitlines()) < 3200
    assert len(definitions) < 65

    expected = {
        "models": "_Position",
        "gates": "_pre_break_decision",
        "virtual": "_advance_virtual_book",
        "portfolio": "_IncrementalPortfolioEquity",
    }
    package = "cta.strategy.multi_timeframe_trend_backtest.engine_components"
    for module_name, symbol in expected.items():
        module = importlib.import_module(f"{package}.{module_name}")
        assert hasattr(module, symbol)
