from __future__ import annotations

import importlib


def test_tushare_client_module_importable() -> None:
    mod = importlib.import_module("cta.data_code.tushare_client")
    assert hasattr(mod, "alpha_prefix")
    assert hasattr(mod, "RateLimiter")
    assert hasattr(mod, "_safe_retry")
