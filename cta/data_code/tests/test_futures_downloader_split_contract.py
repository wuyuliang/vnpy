from __future__ import annotations

import importlib


def test_futures_downloader_split_modules_importable() -> None:
    for mod in (
        "cta.data_code.tushare_client",
        "cta.data_code.futures_downloader",
    ):
        importlib.import_module(mod)


def test_futures_downloader_exports_stable() -> None:
    mod = importlib.import_module("cta.data_code.futures_downloader")
    assert hasattr(mod, "FuturesDownloader")
    assert hasattr(mod, "RateLimiter")
    assert hasattr(mod, "alpha_prefix")
