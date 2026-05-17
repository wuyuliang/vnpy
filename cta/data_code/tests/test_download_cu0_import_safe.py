from __future__ import annotations

import importlib


def test_download_cu0_script_import_has_no_token_side_effect(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    importlib.import_module("cta.data.download_cu0_shf_1min_test")

