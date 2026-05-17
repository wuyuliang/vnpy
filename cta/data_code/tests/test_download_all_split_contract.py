from __future__ import annotations

import importlib


def test_download_all_split_modules_importable() -> None:
    for mod in (
        "cta.data_code.download_all_dispatch",
        "cta.data_code.download_all_progress",
        "cta.data_code.download_all",
    ):
        importlib.import_module(mod)


def test_download_all_public_helpers_still_exposed() -> None:
    mod = importlib.import_module("cta.data_code.download_all")
    assert hasattr(mod, "_normalize_interval_tokens")
    assert hasattr(mod, "_is_financial_symbol")
    assert hasattr(mod, "_resolve_macro_build_symbols")
