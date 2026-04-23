"""§03 回测原则 / Backtest Principles —— 配置 + lookahead 检测.

对应 cta/cta_skills/00_overview_methodology/03_backtest_principles.md §6。

功能
----
1. BacktestConfig 数据类：统一回测参数 schema，所有策略的 engine 应接收它
2. assert_no_lookahead / detect_lookahead: 静态扫描信号是否引用了未来 bar

Lookahead 检测思路（静态 + 启发式）
----------------------------------
真正的 lookahead 是「信号在 t 使用了 t+k（k≥1）的价格/收益」，造成信号
与未来 bar 的收益**直接相关**。本模块提供两个互补检测：

- **严格模式** (`shift_divergence`):
      ret_lagged = signal.shift(1) * fwd_ret(t→t+1)   合规的执行方式
      ret_naive  = signal         * fwd_ret(t→t+1)   若 signal 已经偷看则偏好
  若 `sharpe(naive) - sharpe(lagged)` 相对偏差过大 **且** naive 绝对 sharpe
  非零（避免在纯噪声信号上假阳性），报警。

- **宽松模式** (`corr_with_future`): 信号列与未来 1/2/5 根 bar 的
  **向前收益**（不是 raw close）做 pearson corr，绝对值过高则报警。
  用 return 而不是 raw close，是为了避开价格趋势/漂移对二元信号的干扰。

两种模式都只是启发式，最终还是要靠 code review。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from cta.feature.loader import normalize_interval
from cta.skills import CANON_INTERVALS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    """回测默认参数（所有策略的 engine 都应接收）。"""
    interval: str = "day"
    fill_model: str = "next_open"            # 'next_open' | 'next_close' 禁用 'close'
    slippage_ticks: int = 2
    fee_rate: float = 2e-4                   # 单边手续费（按名义金额 pct）
    start: Optional[str] = "2018-01-01"
    end: Optional[str] = "2024-12-31"
    oos_start: Optional[str] = "2024-01-01"  # 样本外起点

    # 约束
    signal_lag_bars: int = 1                 # 信号 t 使用、t+lag 成交；默认 1
    min_trades_for_stats: int = 30           # 少于此数量不做统计

    def __post_init__(self) -> None:
        canon = normalize_interval(self.interval)
        if canon not in CANON_INTERVALS:
            raise ValueError(
                f"interval={self.interval!r} 不在合法范围 {CANON_INTERVALS}"
            )
        self.interval = canon

        if self.fill_model == "close":
            raise ValueError(
                "fill_model='close' 禁用（会掩盖 lookahead）；请改 'next_open'"
            )
        if self.fill_model not in ("next_open", "next_close"):
            raise ValueError(
                f"fill_model={self.fill_model!r} 不合法（'next_open' | 'next_close'）"
            )

        if self.slippage_ticks < 0:
            raise ValueError("slippage_ticks 必须 ≥ 0")
        if self.fee_rate < 0:
            raise ValueError("fee_rate 必须 ≥ 0")
        if self.signal_lag_bars < 1:
            raise ValueError(
                "signal_lag_bars 必须 ≥ 1（t 产生信号，至少延 1 根 bar 成交）"
            )

        if self.start and self.end and self.start > self.end:
            raise ValueError(f"start ({self.start}) 晚于 end ({self.end})")
        if self.oos_start and self.end and self.oos_start > self.end:
            raise ValueError(f"oos_start ({self.oos_start}) 晚于 end ({self.end})")

    def in_sample_range(self) -> Tuple[Optional[str], Optional[str]]:
        """返回 (start, oos_start-1day) 近似样本内区间；oos_start 为空则返回 (start, end)。"""
        if not self.oos_start:
            return self.start, self.end
        # 简单日期减 1，不做 Timedelta 以保持字符串
        return self.start, self.oos_start

    def out_of_sample_range(self) -> Tuple[Optional[str], Optional[str]]:
        if not self.oos_start:
            return None, None
        return self.oos_start, self.end


@dataclass
class LookaheadViolation:
    mode: str                                # 'shift_divergence' | 'corr_with_future'
    signal: str
    metric: str
    value: float
    threshold: float
    detail: Dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"[{self.mode}] signal={self.signal!r} {self.metric}={self.value:.4f} "
            f"> threshold={self.threshold:.4f}"
        )


# ---------------------------------------------------------------------------
# Lookahead 检测
# ---------------------------------------------------------------------------
def _fwd_returns(close: pd.Series, horizon: int = 1) -> pd.Series:
    """close[t+horizon] / close[t] - 1"""
    return close.shift(-horizon) / close - 1.0


def _safe_sharpe(ret: pd.Series, ann: int = 252) -> float:
    r = ret.dropna()
    if len(r) < 10 or r.std(ddof=0) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=0) * np.sqrt(ann))


def detect_lookahead(
    df: pd.DataFrame,
    signal_col: str,
    close_col: str = "close",
    horizon: int = 1,
    divergence_threshold: float = 0.3,     # 相对 Sharpe 偏差
    corr_threshold: float = 0.15,          # 与未来 close 的相关性绝对值
    horizons_for_corr: Tuple[int, ...] = (1, 2, 5),
) -> List[LookaheadViolation]:
    """
    返回 0~多条违规记录。空 -> 未检测到明显 lookahead。

    严格模式
    --------
    ret_lagged = signal.shift(1) * fwd_ret(t→t+1)   # 合规执行（T 日发信号，T+1 成交）
    ret_naive  = signal          * fwd_ret(t→t+1)   # 偷看当前 bar 收益的版本
    若 sharpe(naive) - sharpe(lagged) 的相对偏差 > divergence_threshold，
    且 sharpe(naive) 绝对值 > 0.5（过滤纯噪声信号）则报警。

    宽松模式
    --------
    对多个 horizon（默认 1/2/5）计算 signal[t] 与 **未来 h 根 bar 的前向收益**
    (close[t+h]/close[t]-1) 的 pearson corr，绝对值 > corr_threshold 报警。
    用 return 而非 raw close，避免价格趋势/漂移干扰二元信号。
    """
    violations: List[LookaheadViolation] = []

    if signal_col not in df.columns:
        raise KeyError(f"signal_col {signal_col!r} 不在 df.columns 中")
    if close_col not in df.columns:
        raise KeyError(f"close_col {close_col!r} 不在 df.columns 中")

    close = df[close_col].astype(float)
    sig = df[signal_col].astype(float).fillna(0.0)

    fwd = _fwd_returns(close, horizon)
    # naive：今天信号 × 今日→明日收益。若信号偷看了 t+k 的价格，naive sharpe
    #        会虚高。
    # lagged：shift 1 根之后才交易，模拟合规执行。
    ret_naive = sig * fwd
    ret_lagged = sig.shift(1) * fwd

    s_naive = _safe_sharpe(ret_naive)
    s_lagged = _safe_sharpe(ret_lagged)
    denom = max(abs(s_lagged), 1e-3)
    diff = (s_naive - s_lagged) / denom  # 正值 = naive 优于 lagged，可疑

    # 只在 naive 有可观绝对 sharpe 时才报；纯噪声信号即便 diff 大也不报
    if diff > divergence_threshold and abs(s_naive) > 0.5:
        violations.append(LookaheadViolation(
            mode="shift_divergence",
            signal=signal_col,
            metric="naive_minus_lagged_sharpe",
            value=float(diff),
            threshold=float(divergence_threshold),
            detail={
                "sharpe_naive": s_naive,
                "sharpe_lagged": s_lagged,
                "horizon": float(horizon),
            },
        ))

    # 与未来 h 根 bar 的前向收益做 pearson corr（不是 raw close）
    for h in horizons_for_corr:
        fwd_h = close.shift(-h) / close - 1.0
        aligned = pd.concat([sig, fwd_h], axis=1).dropna()
        if len(aligned) < 30:
            continue
        a = aligned.iloc[:, 0]
        b = aligned.iloc[:, 1]
        if a.std(ddof=0) == 0 or b.std(ddof=0) == 0:
            continue
        corr = float(a.corr(b))
        if abs(corr) > corr_threshold:
            violations.append(LookaheadViolation(
                mode="corr_with_future",
                signal=signal_col,
                metric=f"corr_fwd_ret_h{h}",
                value=corr,
                threshold=float(corr_threshold),
                detail={"horizon": float(h), "n": float(len(aligned))},
            ))

    return violations


def assert_no_lookahead(
    df: pd.DataFrame,
    signal_col: str,
    **kwargs: float,
) -> None:
    """
    严格版：检测到任何违规即抛 AssertionError。
    Kwargs 透传给 detect_lookahead（divergence_threshold / corr_threshold / ...）。
    """
    vios = detect_lookahead(df, signal_col, **kwargs)
    if vios:
        msg = "\n  ".join(str(v) for v in vios)
        raise AssertionError(f"检测到 lookahead 可疑点 ({len(vios)} 条):\n  {msg}")
