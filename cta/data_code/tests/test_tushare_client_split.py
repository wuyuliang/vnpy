from __future__ import annotations

from functools import partial
import importlib

import pytest


def test_tushare_client_module_importable() -> None:
    mod = importlib.import_module("cta.data_code.tushare_client")
    assert hasattr(mod, "alpha_prefix")
    assert hasattr(mod, "RateLimiter")
    assert hasattr(mod, "_safe_retry")


def test_safe_retry_accepts_partial_callable() -> None:
    mod = importlib.import_module("cta.data_code.tushare_client")

    def always_fail(message: str) -> None:
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="boom"):
        mod._safe_retry(partial(always_fail, "boom"), retries=1, wait=0)
