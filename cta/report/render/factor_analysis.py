"""Alphalens 因子分析（可选依赖）。

只有当用户安装 ``alphalens`` 或 ``alphalens-reloaded`` 时才可用。
未安装时 ``ALPHALENS_AVAILABLE = False``，调用 ``run_alphalens`` 会返回提示而不报错。
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

try:  # pragma: no cover - optional dependency
    import alphalens as al  # type: ignore

    ALPHALENS_AVAILABLE = True
except ImportError:  # pragma: no cover
    al = None
    ALPHALENS_AVAILABLE = False


def run_alphalens(
    factor: pd.Series,
    prices: pd.DataFrame,
    *,
    quantiles: int = 5,
    periods: Iterable[int] = (1, 5, 10),
) -> dict:
    """计算 IC / 分组收益 / 分位数收益。

    factor : MultiIndex(date, asset) -> 因子值
    prices : 列为 asset、行为 date 的价格表

    返回 {available, ic, mean_returns_by_quantile, error}.
    无 alphalens 时 ``available=False``，结果为空。
    """
    if not ALPHALENS_AVAILABLE:
        return {
            "available": False,
            "error": "alphalens 未安装；pip install alphalens-reloaded 后可用",
            "ic": None,
            "mean_returns_by_quantile": None,
        }
    try:
        factor_data = al.utils.get_clean_factor_and_forward_returns(  # type: ignore[attr-defined]
            factor=factor,
            prices=prices,
            quantiles=quantiles,
            periods=tuple(periods),
        )
        ic = al.performance.factor_information_coefficient(factor_data)  # type: ignore[attr-defined]
        mean_q, _ = al.performance.mean_return_by_quantile(factor_data)  # type: ignore[attr-defined]
        return {
            "available": True,
            "error": "",
            "ic": ic,
            "mean_returns_by_quantile": mean_q,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "available": True,
            "error": f"alphalens 计算失败: {e}",
            "ic": None,
            "mean_returns_by_quantile": None,
        }


__all__ = ["ALPHALENS_AVAILABLE", "run_alphalens"]
