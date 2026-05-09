"""收益序列蒙特卡洛重抽样（block bootstrap）。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MonteCarloResult:
    final_equity: dict[float, float]   # {quantile: value}
    max_drawdown: dict[float, float]
    samples_final_equity: np.ndarray   # shape (n_iter,)
    samples_max_dd: np.ndarray         # shape (n_iter,) 正数
    n_iter: int


def _max_dd(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    return float(dd.max())


def block_bootstrap(
    returns: pd.Series | np.ndarray,
    *,
    n_iter: int = 1000,
    block_size: int = 5,
    horizon: int | None = None,
    quantiles: tuple[float, ...] = (0.05, 0.5, 0.95),
    seed: int | None = None,
) -> MonteCarloResult:
    """Block bootstrap returns and summarize final equity / max drawdown distribution.

    block_size 用于保留近邻自相关。horizon 默认与原序列等长。
    """
    arr = np.asarray(returns, dtype=float).flatten()
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        empty = np.zeros(0)
        return MonteCarloResult(
            final_equity={q: 0.0 for q in quantiles},
            max_drawdown={q: 0.0 for q in quantiles},
            samples_final_equity=empty,
            samples_max_dd=empty,
            n_iter=0,
        )

    n = arr.size
    h = int(horizon) if horizon else n
    bs = max(int(block_size), 1)
    rng = np.random.default_rng(seed)
    n_blocks = (h + bs - 1) // bs

    finals = np.empty(n_iter, dtype=float)
    mdds = np.empty(n_iter, dtype=float)
    for k in range(n_iter):
        starts = rng.integers(0, max(n - bs + 1, 1), size=n_blocks)
        sample = np.concatenate([arr[s : s + bs] for s in starts])[:h]
        equity = np.cumsum(sample)
        finals[k] = float(equity[-1]) if equity.size else 0.0
        mdds[k] = _max_dd(equity)

    f_q = {q: float(np.quantile(finals, q)) for q in quantiles}
    d_q = {q: float(np.quantile(mdds, q)) for q in quantiles}
    return MonteCarloResult(
        final_equity=f_q,
        max_drawdown=d_q,
        samples_final_equity=finals,
        samples_max_dd=mdds,
        n_iter=n_iter,
    )


__all__ = ["MonteCarloResult", "block_bootstrap"]
