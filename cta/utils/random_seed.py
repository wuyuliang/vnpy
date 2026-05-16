"""Global random seed utilities."""
from __future__ import annotations

import os
import random
import zlib
from typing import Any

import numpy as np


def _coerce_seed(seed: Any) -> int:
    raw = str(seed).strip()
    try:
        val = int(raw)
    except Exception:
        val = int(zlib.crc32(raw.encode("utf-8")) & 0xFFFFFFFF)
    if val < 0:
        val = -val
    return int(val % (2**32 - 1))


def seed_all(seed: Any) -> int:
    """Seed python/numpy/(optional torch) RNGs and return normalized int seed."""
    s = _coerce_seed(seed)
    random.seed(s)
    np.random.seed(s)
    os.environ["PYTHONHASHSEED"] = str(s)
    try:
        import torch  # type: ignore

        torch.manual_seed(s)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(s)
    except Exception:
        pass
    return s


def seed_all_from_env(env_name: str = "CTA_GLOBAL_SEED", fallback: Any | None = None) -> int | None:
    """Seed RNGs from env var when present; return used seed or None."""
    val = os.environ.get(env_name)
    if val is None or str(val).strip() == "":
        if fallback is None:
            return None
        return seed_all(fallback)
    return seed_all(val)


__all__ = ["seed_all", "seed_all_from_env"]

