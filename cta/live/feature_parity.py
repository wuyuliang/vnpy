"""Feature parity check: online provider vs offline batch source (P0-2).

为什么需要这个模块
------------------
sim/live 的 ``OnlineFeatureLoader`` 在 [online_feature.py](online_feature.py) 是
离线 parquet 读取器。研究阶段的 ``cta/feature`` batch pipeline 也读同一份 parquet
（一般来说），但**真实风险**在于：

- ``OnlineFeatureLoader.load_at`` 按 ≤dt 的最近一行匹配（rolling window 起始点）
- 离线 batch 在 ``merge_asof`` 用 ``signal_datetime`` 拼接
- 边界情况：跨日 / 周末 / NaN 填充 / 时区 / float32 vs float64 量化

如果二者数值差异 > 1e-6，模型在 sim/live 看到的 feature 与训练样本不同 →
``trade_filter_prob`` 漂移 → 完全不同的入场决策。

接口契约（roadmap §2.1 P0-2）
-----------------------------
``compare_online_vs_offline(symbol, dates, interval, online_loader, offline_loader)
→ ParityReport``

验收：1 个 symbol × 1 天 × 60min 上 100+ feature 列 max|diff| < 1e-6；前 N bar
（rolling window 初始化点）允许 NaN。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColumnParityResult:
    """单个 feature 列的 parity 结果。"""
    column: str
    n_compared: int      # 实际参与对比的行数（去除两端 NaN）
    n_both_nan: int      # 双方都 NaN 的行数
    max_abs_diff: float  # max|online - offline|
    is_pass: bool        # max_abs_diff <= tolerance（NaN 视作 0 差异）


@dataclass(frozen=True)
class ParityReport:
    """一次 parity 检查的完整报告。"""
    symbol: str
    interval: str
    n_timestamps: int
    n_columns: int
    n_columns_pass: int
    n_columns_fail: int
    column_results: tuple[ColumnParityResult, ...]
    overall_pass: bool

    @property
    def failing_columns(self) -> tuple[str, ...]:
        return tuple(r.column for r in self.column_results if not r.is_pass)


# Online / offline loader 协议：(symbol, interval, dt) -> pd.Series | None
LoaderFn = Callable[[str, str, pd.Timestamp], "pd.Series | None"]


def _diff_series(a: pd.Series, b: pd.Series) -> tuple[float, int, int]:
    """计算两 Series 的 max abs diff。返回 (max_abs_diff, n_compared, n_both_nan)。"""
    if len(a) != len(b):
        raise ValueError(f"Series length mismatch: {len(a)} vs {len(b)}")
    a_arr = pd.to_numeric(a, errors="coerce").astype(float).values
    b_arr = pd.to_numeric(b, errors="coerce").astype(float).values
    nan_a = np.isnan(a_arr)
    nan_b = np.isnan(b_arr)
    both_nan = int(np.sum(nan_a & nan_b))
    # 只在双方都非 NaN 的位置比较；其余视作差异 0（rolling warmup 允许）
    both_valid = ~(nan_a | nan_b)
    n_compared = int(np.sum(both_valid))
    if n_compared == 0:
        return 0.0, 0, both_nan
    diff = np.abs(a_arr[both_valid] - b_arr[both_valid])
    return float(diff.max()) if diff.size else 0.0, n_compared, both_nan


def compare_online_vs_offline(
    *,
    symbol: str,
    interval: str,
    dates: Iterable[pd.Timestamp],
    online_loader: LoaderFn,
    offline_loader: LoaderFn,
    columns: Iterable[str] | None = None,
    tolerance: float = 1e-6,
) -> ParityReport:
    """对比 online vs offline 在多个时点的 feature。

    Args:
        symbol: e.g. "RB0"
        interval: e.g. "60min" / "day"
        dates: 要比对的 timestamp 列表
        online_loader / offline_loader: ``LoaderFn``，返回 (symbol, interval, dt) → Series
        columns: 限定参与对比的列；None = 取双方共有列
        tolerance: 单列 max_abs_diff 通过门槛

    Returns:
        ParityReport with per-column results.
    """
    timestamps = [pd.Timestamp(d) for d in dates]
    if not timestamps:
        return ParityReport(symbol, interval, 0, 0, 0, 0, (), True)

    online_rows: list[pd.Series] = []
    offline_rows: list[pd.Series] = []
    for ts in timestamps:
        on = online_loader(symbol, interval, ts)
        off = offline_loader(symbol, interval, ts)
        # 用空 Series 占位；NaN 差异在 _diff_series 中处理
        online_rows.append(on if on is not None else pd.Series(dtype=float))
        offline_rows.append(off if off is not None else pd.Series(dtype=float))

    online_df = pd.DataFrame(online_rows)
    offline_df = pd.DataFrame(offline_rows)
    common_cols = sorted(set(online_df.columns) & set(offline_df.columns))
    if columns is not None:
        wanted = {str(c) for c in columns}
        common_cols = [c for c in common_cols if c in wanted]

    if not common_cols:
        logger.warning(
            "no common columns between online and offline for %s/%s", symbol, interval
        )
        return ParityReport(symbol, interval, len(timestamps), 0, 0, 0, (), True)

    results: list[ColumnParityResult] = []
    for col in common_cols:
        max_diff, n_cmp, n_both_nan = _diff_series(online_df[col], offline_df[col])
        is_pass = max_diff <= float(tolerance)
        results.append(
            ColumnParityResult(
                column=col,
                n_compared=n_cmp,
                n_both_nan=n_both_nan,
                max_abs_diff=max_diff,
                is_pass=bool(is_pass),
            )
        )

    n_pass = sum(1 for r in results if r.is_pass)
    n_fail = len(results) - n_pass
    return ParityReport(
        symbol=symbol,
        interval=interval,
        n_timestamps=len(timestamps),
        n_columns=len(results),
        n_columns_pass=n_pass,
        n_columns_fail=n_fail,
        column_results=tuple(results),
        overall_pass=(n_fail == 0),
    )


__all__ = [
    "ColumnParityResult",
    "LoaderFn",
    "ParityReport",
    "compare_online_vs_offline",
]
