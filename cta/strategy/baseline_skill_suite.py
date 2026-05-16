"""Baseline skill suite for vn.py CTA research.

本模块实现 4 套纯规则 baseline，并支持把已成交交易转换为训练样本：
1. Donchian breakout
2. ATR breakout
3. tight range breakout
4. breakout pullback continuation

策略假设
--------
1. 纯规则基线可提供稳定可解释的候选交易样本。
2. 候选交易的 entry 时点特征可用于后续 ML 训练（trade filter / regime）。
3. 先构建 baseline，再叠加模型，更利于排查风险来源。

适用品种
--------
中国商品连续合约（RB0/HC0/MA0/...）。

适用周期
--------
day/minute60/minute30/minute15/minute5/minute。

开平仓与风控摘要
--------------
1. Donchian：突破开仓，反向通道平仓。
2. ATR breakout：通道突破开仓，回到中轨平仓。
3. Tight range breakout：窄幅突破 stop 入场，ATR 跟踪止损/超时退出。
4. Breakout pullback：突破后回踩确认入场，ATR 跟踪止损/超时退出。
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import (
    BASELINE_SIGNAL_TYPES,
    LABEL_MAE_PENALTY,
    LABEL_THRESHOLD,
    TRAINING_FEATURE_COLUMNS,
)
from cta.config.skill_tight_range_breakout_config import BacktestConfig, StrategyConfig, VALID_SIDE_MODES
from cta.skills.data_backtest.event_driven_backtest import EngineConfig, run_backtest
from cta.skills.data_backtest.trade_evaluation import summarize_trades
from cta.skills.data_backtest.transaction_cost import estimate_cost
from cta.skills.price_action.breakout_pullback import (
    PullbackSetup,
    detect_breakout_pullback,
    pullback_entry_trigger,
)
from cta.skills.price_action.tight_range_breakout import (
    TightRangeSetup,
    resolve_breakout_trigger,
)
from cta.skills.trend_strategies.atr_breakout import compute_atr_channel
from cta.skills.trend_strategies.donchian_breakout import compute_donchian
from cta.strategy.skill_tight_range_backtest import (
    build_contract_spec,
    load_bars,
    normalize_interval,
    resolve_exchange,
    suggest_periods_per_year,
)
from cta.strategy.skill_tight_range_breakout import (
    ContractSpec,
    SkillTightRangeBreakoutStrategy,
    prepare_strategy_frame,
)

logger = logging.getLogger(__name__)

SYMBOLS_RANKING_PATH = Path(__file__).resolve().parents[1] / "feature" / "symbols_research_ranking.csv"


@dataclass(frozen=True)
class BaselineSuiteRunResult:
    output_dir: Path
    summary_path: Path
    training_samples_path: Path
    report_path: Path


def _normalize_intervals(intervals: str | Sequence[str]) -> tuple[str, ...]:
    """Normalize one-or-many interval args.

    支持：
    - 单值：``"60min"``
    - 多值空格：``["day", "60min", "30min"]``
    - 逗号混合：``["day,60min", "30min,15min"]``
    """
    if isinstance(intervals, str):
        raw_tokens: list[str] = [intervals]
    else:
        raw_tokens = [str(x) for x in intervals]

    parts: list[str] = []
    for tk in raw_tokens:
        parts.extend([p.strip() for p in str(tk).split(",") if p.strip()])

    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        canon = normalize_interval(p)
        if canon not in seen:
            seen.add(canon)
            out.append(canon)
    return tuple(out)


def _load_top_n_symbols_from_ranking(ranking_path: Path, top_n: int) -> list[tuple[str, str]]:
    """Load top-N symbols ordered by ``research_rank`` from ranking csv."""
    if int(top_n) <= 0:
        return []
    if not ranking_path.exists():
        raise FileNotFoundError(f"symbols ranking csv not found: {ranking_path}")

    df = pd.read_csv(ranking_path, encoding="utf-8-sig")
    need = {"symbol", "exchange", "research_rank"}
    miss = need - set(df.columns)
    if miss:
        raise ValueError(f"ranking csv missing columns: {sorted(miss)}")

    view = df[["symbol", "exchange", "research_rank"]].copy()
    view["symbol"] = view["symbol"].astype(str).str.strip().str.upper()
    view["exchange"] = view["exchange"].astype(str).str.strip().str.upper()
    view["research_rank"] = pd.to_numeric(view["research_rank"], errors="coerce")
    view = view.dropna(subset=["symbol", "exchange", "research_rank"])
    view = view.sort_values("research_rank").drop_duplicates(subset=["symbol", "exchange"], keep="first")
    view = view.head(int(top_n)).reset_index(drop=True)
    return [(str(r["symbol"]), str(r["exchange"])) for _, r in view.iterrows()]


def _resolve_run_exchange(exchange_from_rank: str | None, cli_exchange: str | None) -> str | None:
    rank_ex = str(exchange_from_rank).strip().upper() if exchange_from_rank else None
    cli_ex = str(cli_exchange).strip().upper() if cli_exchange else None
    return rank_ex or cli_ex


def _safe_float(v: Any) -> float:
    try:
        fv = float(v)
    except Exception:
        return float("nan")
    return fv


def _safe_bool(v: Any) -> bool:
    """B4 fix: bool(np.nan) == True 是 Python 的隐藏陷阱；
    把 NaN / None / 异常输入统一收敛成 False，避免无效 setup 被误判为 valid。
    """
    if v is None:
        return False
    try:
        if isinstance(v, float) and np.isnan(v):
            return False
        if isinstance(v, np.floating) and bool(np.isnan(v)):
            return False
    except Exception:
        return False
    try:
        return bool(v)
    except Exception:
        return False


def _compute_atr14(df: pd.DataFrame) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=7).mean()


def _side_allowed(mode: str, side: str) -> bool:
    m = str(mode).strip().lower()
    s = str(side).strip().lower()
    if m == "both":
        return s in {"long", "short"}
    if m == "long":
        return s == "long"
    if m == "short":
        return s == "short"
    return False


def _entry_order(
    contract: ContractSpec,
    side: str,
    lots: int,
    order_type: str,
    price: float | None = None,
) -> dict[str, Any]:
    od: dict[str, Any] = {
        "side": str(side),
        "lots": int(max(1, lots)),
        "order_type": str(order_type),
        "symbol": contract.vt_symbol,
        "multiplier": float(contract.multiplier),
        "commission_rate": float(contract.commission_rate),
        "tick_size": float(contract.tick_size),
    }
    if price is not None and np.isfinite(float(price)):
        od["price"] = float(price)
    return od


def prepare_master_feature_frame(
    bars: pd.DataFrame,
    interval: str = "day",
    *,
    symbol: str | None = None,
    pricetick: float | None = None,
) -> pd.DataFrame:
    """Prepare one dataframe containing baseline features for all strategies."""
    out = bars.copy().reset_index(drop=True)
    if "datetime" in out.columns:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
        out = (
            out.dropna(subset=["datetime"])
            .sort_values("datetime")
            .drop_duplicates("datetime", keep="last")
            .reset_index(drop=True)
        )

    for c in ("open", "high", "low", "close", "volume"):
        if c not in out.columns:
            raise KeyError(f"prepare_master_feature_frame missing column: {c}")
        out[c] = out[c].astype(float)
    if "open_interest" not in out.columns:
        out["open_interest"] = 0.0
    if "turnover" not in out.columns:
        out["turnover"] = 0.0

    # 涨跌停 / 一字板标记。P0.2 重写：
    # 1) one_way_bar：(high - low) ≤ max(pricetick, 0.0005 × close)，
    #    覆盖"严格 high==low" 与"high-low < 1 tick"两种真实数据形态；
    # 2) limit_up/down：close 相对前收变化 ≥ cluster 涨跌停比例 - 0.1%（留 buffer
    #    避免边界因 tick rounding 漏掉）。symbol 缺失时用 5% 保守 proxy。
    prev_close = out["close"].shift(1)
    close_s = out["close"].astype(float)
    if pricetick is None or not np.isfinite(float(pricetick)) or float(pricetick) <= 0:
        # 无显式 pricetick 时按 close 的 5bp 反推；商品最小 tick 通常 < 0.05% 价格
        tick_ref = (close_s * 0.0005).abs()
    else:
        tick_ref = pd.Series(
            np.full(len(out), float(pricetick), dtype=float),
            index=out.index,
        )
    one_way = (out["high"] - out["low"]).abs() <= tick_ref

    from cta.config.symbol_cluster_config import infer_symbol_limit_pct

    limit_pct = float(infer_symbol_limit_pct(str(symbol) if symbol else "", default_pct=0.05))
    # buffer 0.1%（绝对值），避免 close=prev_close*1.0399 与 limit 0.04 因 tick rounding 误差被漏掉
    buffer_abs = 0.001
    pct_change = (close_s / prev_close - 1.0).where(prev_close.notna())
    limit_up = pct_change >= (limit_pct - buffer_abs)
    limit_down = pct_change <= -(limit_pct - buffer_abs)
    # 注意：close 触达涨跌停时不要求 one_way，因为很多一字板在 9:00 集合竞价后
    # 当天仍有少量成交，high>low 但 close 与开盘一致 — 模型仍需识别为风险。
    out["is_one_way_bar"] = one_way.astype(int)
    out["is_limit_up_close"] = limit_up.fillna(False).astype(int)
    out["is_limit_down_close"] = limit_down.fillna(False).astype(int)

    out["atr14"] = _compute_atr14(out)

    don_df = compute_donchian(out, n_entry=55, n_exit=20, interval=interval)
    for c in ("don_upper_entry", "don_lower_entry", "don_upper_exit", "don_lower_exit", "don_atr20"):
        out[c] = don_df[c].astype(float)

    atr_df = compute_atr_channel(out, ma_n=20, atr_n=14, k=2.5, interval=interval)
    for c in ("atr_ma", "atr_value", "atr_upper", "atr_lower"):
        out[c] = atr_df[c].astype(float)

    tr_cfg = StrategyConfig(
        lookback=10,
        alpha=1.5,
        min_count=5,
        min_breakout_score=0.30,
        lots=1,
        risk_per_trade_pct=0.005,
        initial_stop_atr_mult=1.5,
        trailing_stop_atr_mult=2.2,
        max_holding_bars=20,
        align_trend_direction=False,
        trade_side_mode="both",
    )
    tr_df = prepare_strategy_frame(out, tr_cfg, interval=interval)
    if len(tr_df) != len(out):
        raise ValueError(f"prepare_strategy_frame rows mismatch: out={len(out)} tr_df={len(tr_df)}")
    for c in (
        "tr_valid",
        "tr_upper",
        "tr_lower",
        "tr_range_atr",
        "tr_count",
        "tr_direction_bias",
        "trend_score",
        "trend_dir",
        "trend_strength",
        "breakout_score",
        "breakout_pass",
    ):
        if c in tr_df.columns:
            out[c] = tr_df[c]

    anchor = pd.DataFrame(index=out.index)
    anchor["breakout_level"] = np.nan
    anchor["breakout_direction"] = ""
    long_mask = out["close"].astype(float) > out["don_upper_entry"].astype(float)
    short_mask = out["close"].astype(float) < out["don_lower_entry"].astype(float)
    anchor.loc[long_mask, "breakout_level"] = out.loc[long_mask, "don_upper_entry"]
    anchor.loc[long_mask, "breakout_direction"] = "long"
    anchor.loc[short_mask, "breakout_level"] = out.loc[short_mask, "don_lower_entry"]
    anchor.loc[short_mask, "breakout_direction"] = "short"

    pb_df = detect_breakout_pullback(
        out,
        breakout_df=anchor,
        max_bars_since_brk=20,
        max_pullback_atr=1.5,
        interval=interval,
    )
    for c in (
        "bp_valid",
        "bp_direction",
        "bp_breakout_level",
        "bp_pullback_low",
        "bp_bars_since_breakout",
        "bp_confirmed",
    ):
        out[c] = pb_df[c]

    return out


class DonchianBaselineStrategy:
    """Donchian breakout baseline."""

    def __init__(self, frame: pd.DataFrame, contract: ContractSpec, trade_side_mode: str = "both") -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        if i <= 0:
            return []
        close = _safe_float(bar.get("close"))
        ue = _safe_float(bar.get("don_upper_entry"))
        le = _safe_float(bar.get("don_lower_entry"))
        ux = _safe_float(bar.get("don_upper_exit"))
        lx = _safe_float(bar.get("don_lower_exit"))
        tick = float(abs(self.contract.tick_size))

        if position == 0:
            if np.isfinite(ue) and close > ue and _side_allowed(self.trade_side_mode, "long"):
                return [_entry_order(self.contract, "long", lots=1, order_type="stop", price=float(bar["high"]) + tick)]
            if np.isfinite(le) and close < le and _side_allowed(self.trade_side_mode, "short"):
                return [_entry_order(self.contract, "short", lots=1, order_type="stop", price=float(bar["low"]) - tick)]
            return []

        if position > 0 and np.isfinite(lx) and close < lx:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        if position < 0 and np.isfinite(ux) and close > ux:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        return []


class ATRBreakoutBaselineStrategy:
    """ATR channel breakout baseline."""

    def __init__(self, frame: pd.DataFrame, contract: ContractSpec, trade_side_mode: str = "both") -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        if i <= 0:
            return []
        prev = self.frame.iloc[i - 1]
        close = _safe_float(bar.get("close"))
        up = _safe_float(bar.get("atr_upper"))
        lo = _safe_float(bar.get("atr_lower"))
        ma = _safe_float(bar.get("atr_ma"))
        pup = _safe_float(prev.get("atr_upper"))
        plo = _safe_float(prev.get("atr_lower"))
        pclose = _safe_float(prev.get("close"))

        if position == 0:
            if np.isfinite(up) and np.isfinite(pup) and close > up and pclose <= pup:
                if _side_allowed(self.trade_side_mode, "long"):
                    return [_entry_order(self.contract, "long", lots=1, order_type="market")]
            if np.isfinite(lo) and np.isfinite(plo) and close < lo and pclose >= plo:
                if _side_allowed(self.trade_side_mode, "short"):
                    return [_entry_order(self.contract, "short", lots=1, order_type="market")]
            return []

        if position > 0 and np.isfinite(ma) and close < ma:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        if position < 0 and np.isfinite(ma) and close > ma:
            return [_entry_order(self.contract, "flat", lots=abs(position), order_type="market")]
        return []


class BreakoutPullbackBaselineStrategy:
    """Breakout-pullback continuation baseline with ATR trailing exit."""

    def __init__(
        self,
        frame: pd.DataFrame,
        contract: ContractSpec,
        trade_side_mode: str = "both",
        max_holding_bars: int = 30,
        trailing_stop_atr_mult: float = 2.0,
        initial_stop_atr_mult: float = 1.2,
    ) -> None:
        self.frame = frame.reset_index(drop=True)
        self.contract = contract
        self.trade_side_mode = trade_side_mode
        self.max_holding_bars = int(max_holding_bars)
        self.trailing_stop_atr_mult = float(trailing_stop_atr_mult)
        self.initial_stop_atr_mult = float(initial_stop_atr_mult)

        self._prev_position: int = 0
        self._entry_index: int = -1
        self._high_since_entry: float = float("-inf")
        self._low_since_entry: float = float("inf")
        self._stop_price: float | None = None
        self._pending_initial_stop: float | None = None

    def _sync(self, i: int, bar: pd.Series, position: int) -> None:
        if self._prev_position == 0 and position != 0:
            atr_v = _safe_float(bar.get("atr14"))
            if not np.isfinite(atr_v) or atr_v <= 0:
                atr_v = max(_safe_float(bar.get("high")) - _safe_float(bar.get("low")), self.contract.tick_size)
            self._entry_index = i
            self._high_since_entry = _safe_float(bar.get("high"))
            self._low_since_entry = _safe_float(bar.get("low"))
            if position > 0:
                fallback = _safe_float(bar.get("open")) - self.initial_stop_atr_mult * atr_v
                self._stop_price = (
                    max(float(self._pending_initial_stop), fallback)
                    if self._pending_initial_stop is not None
                    else fallback
                )
            else:
                fallback = _safe_float(bar.get("open")) + self.initial_stop_atr_mult * atr_v
                self._stop_price = (
                    min(float(self._pending_initial_stop), fallback)
                    if self._pending_initial_stop is not None
                    else fallback
                )
            self._pending_initial_stop = None
        if self._prev_position != 0 and position == 0:
            self._entry_index = -1
            self._high_since_entry = float("-inf")
            self._low_since_entry = float("inf")
            self._stop_price = None
            self._pending_initial_stop = None

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        self._sync(i, bar, position)
        tick = float(abs(self.contract.tick_size))

        if position == 0:
            if not _safe_bool(bar.get("bp_valid", False)) or not _safe_bool(bar.get("bp_confirmed", False)):
                self._prev_position = int(position)
                return []
            direction = str(bar.get("bp_direction", "")).lower()
            if direction not in {"long", "short"} or (not _side_allowed(self.trade_side_mode, direction)):
                self._prev_position = int(position)
                return []
            setup = PullbackSetup(
                valid=_safe_bool(bar.get("bp_valid", False)),
                direction=direction,  # type: ignore[arg-type]
                breakout_level=_safe_float(bar.get("bp_breakout_level")),
                pullback_low=_safe_float(bar.get("bp_pullback_low")),
                bars_since_breakout=int(_safe_float(bar.get("bp_bars_since_breakout"))),
                confirmed=_safe_bool(bar.get("bp_confirmed", False)),
            )
            trig = pullback_entry_trigger(setup, bar, tick_size=tick)
            self._prev_position = int(position)
            if trig is None:
                return []
            self._pending_initial_stop = float(trig["stop"])
            return [
                _entry_order(
                    self.contract,
                    side=str(trig["side"]),
                    lots=1,
                    order_type="stop",
                    price=float(trig["trigger"]),
                )
            ]

        atr_v = _safe_float(bar.get("atr14"))
        if (not np.isfinite(atr_v)) or atr_v <= 0:
            atr_v = max(_safe_float(bar.get("high")) - _safe_float(bar.get("low")), self.contract.tick_size)
        bars_held = i - self._entry_index if self._entry_index >= 0 else 0
        high = _safe_float(bar.get("high"))
        low = _safe_float(bar.get("low"))

        if position > 0:
            self._high_since_entry = max(self._high_since_entry, high)
            trail = self._high_since_entry - self.trailing_stop_atr_mult * atr_v
            self._stop_price = trail if self._stop_price is None else max(float(self._stop_price), trail)
            stop_hit = low <= float(self._stop_price)
        else:
            self._low_since_entry = min(self._low_since_entry, low)
            trail = self._low_since_entry + self.trailing_stop_atr_mult * atr_v
            self._stop_price = trail if self._stop_price is None else min(float(self._stop_price), trail)
            stop_hit = high >= float(self._stop_price)
        if stop_hit or bars_held >= self.max_holding_bars:
            self._prev_position = int(position)
            return [_entry_order(self.contract, side="flat", lots=abs(position), order_type="market")]

        self._prev_position = int(position)
        return []


def create_baseline_strategy(
    signal_type: str,
    frame: pd.DataFrame,
    contract: ContractSpec,
    trade_side_mode: str = "both",
) -> Any:
    """Factory for baseline strategy objects."""
    st = str(signal_type).strip().lower()
    mode = str(trade_side_mode).strip().lower()
    if mode not in VALID_SIDE_MODES:
        raise ValueError(f"invalid trade_side_mode={trade_side_mode}, valid={sorted(VALID_SIDE_MODES)}")

    if st == "donchian_breakout":
        return DonchianBaselineStrategy(frame=frame, contract=contract, trade_side_mode=mode)
    if st == "atr_breakout":
        return ATRBreakoutBaselineStrategy(frame=frame, contract=contract, trade_side_mode=mode)
    if st == "tight_range_breakout":
        cfg = StrategyConfig(
            lookback=10,
            alpha=1.5,
            min_count=5,
            min_breakout_score=0.30,
            lots=1,
            risk_per_trade_pct=0.005,
            initial_stop_atr_mult=1.5,
            trailing_stop_atr_mult=2.2,
            max_holding_bars=20,
            align_trend_direction=False,
            trade_side_mode=mode,
        )
        return SkillTightRangeBreakoutStrategy(frame=frame, cfg=cfg, contract=contract, capital_base=1_000_000.0)
    if st == "breakout_pullback_continuation":
        return BreakoutPullbackBaselineStrategy(frame=frame, contract=contract, trade_side_mode=mode)
    raise ValueError(f"unsupported signal_type={signal_type}, valid={BASELINE_SIGNAL_TYPES}")


def build_training_samples_from_trade_log(
    trade_log: pd.DataFrame,
    frame: pd.DataFrame,
    symbol: str,
    exchange: str,
    interval: str,
    signal_type: str,
    feature_columns: tuple[str, ...] = TRAINING_FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Convert executed trades into ML-friendly samples."""
    if trade_log.empty:
        cols = [
            "symbol",
            "exchange",
            "interval",
            "datetime",
            # L2 fix：写出 signal_datetime（signal bar 时间），下游 merge_asof 用作
            # generic 特征 merge key，避免 next-bar lookahead。
            "signal_datetime",
            "signal_type",
            "side",
            "signal_i",
            "entry_i",
            "exit_i",
            "holding_bars",
            "entry_price",
            "exit_price",
            "label_gross_pnl",
            "label_cost",
            "label_net_pnl",
            "label_win",
            "label_mfe_atr",
            "label_mae_atr",
            # D6 fix：trade_log 路径也要写 atr_warmed，否则下游
            # _ensure_training_columns 默认填 1，会把 ATR warmup 期的样本误带进训练。
            "atr_warmed",
        ] + [f"feature_{c}" for c in feature_columns]
        return pd.DataFrame(columns=cols)

    rows: list[dict[str, Any]] = []
    dt_series = pd.to_datetime(frame.get("datetime", pd.Series([pd.NaT] * len(frame))), errors="coerce")
    for _, tr in trade_log.iterrows():
        entry_i = int(_safe_float(tr.get("entry_i", -1)))
        exit_i = int(_safe_float(tr.get("exit_i", -1)))
        if entry_i < 0 or exit_i < entry_i or entry_i >= len(frame):
            continue
        exit_i = min(exit_i, len(frame) - 1)
        # L2 fix：决策时刻是 signal_bar = entry_bar 之前 1 根。模型推理时只能看
        # signal_bar 的特征，训练时也必须从 signal_bar 取，否则就是 next-bar leak。
        signal_i = max(0, entry_i - 1)
        signal_row = frame.iloc[signal_i]
        entry_row = frame.iloc[entry_i]
        entry_price = _safe_float(tr.get("entry_price", entry_row.get("close", np.nan)))
        exit_price = _safe_float(tr.get("exit_price", frame.iloc[exit_i].get("close", np.nan)))
        side = str(tr.get("side", "")).lower()
        if side not in {"long", "short"}:
            continue
        seg = frame.iloc[entry_i : exit_i + 1]
        seg_high = seg["high"].astype(float).max()
        seg_low = seg["low"].astype(float).min()
        # 标签端 mfe/mae 仍按 entry_bar 的 atr14 归一化，与候选 scan 路径口径一致。
        atr_entry = _safe_float(entry_row.get("atr14", np.nan))

        if side == "short":
            mfe = entry_price - seg_low
            mae = seg_high - entry_price
        else:
            mfe = seg_high - entry_price
            mae = entry_price - seg_low
        mfe_atr = mfe / atr_entry if np.isfinite(atr_entry) and atr_entry > 0 else np.nan
        mae_atr = mae / atr_entry if np.isfinite(atr_entry) and atr_entry > 0 else np.nan
        # D6 fix：entry bar 的 atr14 不可用（NaN 或非正）就视为 ATR 还在 warmup。
        atr_warmed_flag = int(np.isfinite(atr_entry) and atr_entry > 0)

        sample: dict[str, Any] = {
            "symbol": str(symbol).upper(),
            "exchange": str(exchange).upper(),
            "interval": str(interval),
            "datetime": dt_series.iloc[entry_i],
            # L2 fix：signal_datetime 让下游 merge_asof 拼 generic 特征时能对齐
            # signal bar，避免 next-bar lookahead leak。
            "signal_datetime": dt_series.iloc[signal_i],
            "signal_type": str(signal_type),
            "side": side,
            "signal_i": signal_i,
            "entry_i": entry_i,
            "exit_i": exit_i,
            "holding_bars": int(exit_i - entry_i),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "label_gross_pnl": _safe_float(tr.get("gross_pnl", np.nan)),
            "label_cost": _safe_float(tr.get("cost", np.nan)),
            "label_net_pnl": _safe_float(tr.get("net_pnl", np.nan)),
            "label_win": int(_safe_float(tr.get("net_pnl", 0.0)) > 0.0),
            "label_mfe_atr": mfe_atr,
            "label_mae_atr": mae_atr,
            "atr_warmed": atr_warmed_flag,
        }
        for c in feature_columns:
            # L2 fix：feature_* 必须从 signal_row 取（决策时刻特征），而不是 entry_row。
            sample[f"feature_{c}"] = signal_row[c] if c in frame.columns else np.nan
        rows.append(sample)
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("datetime").reset_index(drop=True)
    return out


def _infer_regime_label(row: pd.Series) -> str:
    raw = row.get("regime_label", "")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    trend_dir = _safe_float(row.get("trend_dir", np.nan))
    if np.isfinite(trend_dir):
        if trend_dir > 0:
            return "trend_up"
        if trend_dir < 0:
            return "trend_down"

    trend_score = _safe_float(row.get("trend_score", np.nan))
    if np.isfinite(trend_score):
        if trend_score > 0.2:
            return "trend_up"
        if trend_score < -0.2:
            return "trend_down"
    return "range"


def _resolve_candidate_entry(
    order: dict[str, Any],
    next_bar: pd.Series,
) -> tuple[bool, float, float]:
    side = str(order.get("side", "")).strip().lower()
    order_type = str(order.get("order_type", "market")).strip().lower()
    next_open = _safe_float(next_bar.get("open", np.nan))
    next_high = _safe_float(next_bar.get("high", np.nan))
    next_low = _safe_float(next_bar.get("low", np.nan))

    if order_type == "market":
        if not np.isfinite(next_open):
            return False, float("nan"), float("nan")
        return True, float(next_open), float("nan")

    trigger = _safe_float(order.get("price", np.nan))
    if not np.isfinite(trigger):
        return False, float("nan"), float("nan")

    if side == "long":
        if not np.isfinite(next_high) or next_high < trigger:
            return False, float("nan"), float(trigger)
        entry_price = max(next_open, trigger) if np.isfinite(next_open) else trigger
        return True, float(entry_price), float(trigger)
    if side == "short":
        if not np.isfinite(next_low) or next_low > trigger:
            return False, float("nan"), float(trigger)
        entry_price = min(next_open, trigger) if np.isfinite(next_open) else trigger
        return True, float(entry_price), float(trigger)
    return False, float("nan"), float("nan")


def _simulate_candidate_execution_path(
    frame: pd.DataFrame,
    *,
    entry_i: int,
    horizon_i: int,
    side: str,
    entry_price: float,
    atr_v: float,
    stop_loss_pct: float,
) -> dict[str, Any]:
    """Simulate candidate execution with stop-aware path (no lookahead order ambiguity)."""
    seg = frame.iloc[entry_i : horizon_i + 1]
    if seg.empty or not np.isfinite(entry_price):
        return {
            "exit_i": horizon_i,
            "exit_price": float("nan"),
            "mfe_atr": float("nan"),
            "mae_atr": float("nan"),
            "pnl_atr": float("nan"),
            "stop_hit": False,
        }

    side_l = str(side).strip().lower()
    stop_pct = max(0.0, float(stop_loss_pct))
    if side_l == "short":
        stop_price = entry_price * (1.0 + stop_pct)
    else:
        stop_price = entry_price * (1.0 - stop_pct)

    actual_exit_i = int(horizon_i)
    exit_price = _safe_float(seg.iloc[-1].get("close", np.nan))
    stop_hit = False

    for j in range(entry_i, horizon_i + 1):
        row = frame.iloc[j]
        bar_open = _safe_float(row.get("open", np.nan))
        bar_high = _safe_float(row.get("high", np.nan))
        bar_low = _safe_float(row.get("low", np.nan))
        if side_l == "short":
            if np.isfinite(bar_high) and bar_high >= stop_price:
                stop_hit = True
                actual_exit_i = j
                exit_price = max(stop_price, bar_open) if np.isfinite(bar_open) else stop_price
                break
        else:
            if np.isfinite(bar_low) and bar_low <= stop_price:
                stop_hit = True
                actual_exit_i = j
                exit_price = min(stop_price, bar_open) if np.isfinite(bar_open) else stop_price
                break

    exec_seg = frame.iloc[entry_i : actual_exit_i + 1]
    seg_high = float(exec_seg["high"].astype(float).max())
    seg_low = float(exec_seg["low"].astype(float).min())
    if side_l == "short":
        mfe = entry_price - seg_low
        mae = seg_high - entry_price
        pnl = entry_price - exit_price if np.isfinite(exit_price) else np.nan
    else:
        mfe = seg_high - entry_price
        mae = entry_price - seg_low
        pnl = exit_price - entry_price if np.isfinite(exit_price) else np.nan

    if np.isfinite(atr_v) and atr_v > 0:
        mfe_atr = float(mfe / atr_v)
        mae_atr = float(mae / atr_v)
        pnl_atr = float(pnl / atr_v) if np.isfinite(pnl) else float("nan")
    else:
        mfe_atr = float("nan")
        mae_atr = float("nan")
        pnl_atr = float("nan")
    return {
        "exit_i": int(actual_exit_i),
        "exit_price": float(exit_price) if np.isfinite(exit_price) else float("nan"),
        "mfe_atr": mfe_atr,
        "mae_atr": mae_atr,
        "pnl_atr": pnl_atr,
        "stop_hit": bool(stop_hit),
    }


def _build_raw_setup_candidates(
    frame: pd.DataFrame,
    i: int,
    signal_type: str,
    contract: ContractSpec,
    mode: str,
) -> list[dict[str, Any]]:
    bar = frame.iloc[i]
    tick = float(abs(contract.tick_size))
    out: list[dict[str, Any]] = []

    if signal_type == "donchian_breakout":
        close = _safe_float(bar.get("close"))
        ue = _safe_float(bar.get("don_upper_entry"))
        le = _safe_float(bar.get("don_lower_entry"))
        if np.isfinite(ue) and close > ue:
            out.append(
                {
                    "side": "long",
                    "order_type": "stop",
                    "trigger": _safe_float(bar.get("high")) + tick,
                    "filtered_reason": None if _side_allowed(mode, "long") else "side_mode",
                }
            )
        if np.isfinite(le) and close < le:
            out.append(
                {
                    "side": "short",
                    "order_type": "stop",
                    "trigger": _safe_float(bar.get("low")) - tick,
                    "filtered_reason": None if _side_allowed(mode, "short") else "side_mode",
                }
            )
        return out

    if signal_type == "atr_breakout":
        if i <= 0:
            return out
        prev = frame.iloc[i - 1]
        close = _safe_float(bar.get("close"))
        up = _safe_float(bar.get("atr_upper"))
        lo = _safe_float(bar.get("atr_lower"))
        pup = _safe_float(prev.get("atr_upper"))
        plo = _safe_float(prev.get("atr_lower"))
        pclose = _safe_float(prev.get("close"))
        if np.isfinite(up) and np.isfinite(pup) and close > up and pclose <= pup:
            out.append(
                {
                    "side": "long",
                    "order_type": "market",
                    "trigger": np.nan,
                    "filtered_reason": None if _side_allowed(mode, "long") else "side_mode",
                }
            )
        if np.isfinite(lo) and np.isfinite(plo) and close < lo and pclose >= plo:
            out.append(
                {
                    "side": "short",
                    "order_type": "market",
                    "trigger": np.nan,
                    "filtered_reason": None if _side_allowed(mode, "short") else "side_mode",
                }
            )
        return out

    if signal_type == "tight_range_breakout":
        valid = _safe_bool(bar.get("tr_valid", False))
        if not valid:
            return out
        close = _safe_float(bar.get("close"))
        upper = _safe_float(bar.get("tr_upper"))
        lower = _safe_float(bar.get("tr_lower"))
        if np.isfinite(upper) and close >= upper:
            side = "long"
        elif np.isfinite(lower) and close <= lower:
            side = "short"
        else:
            side = "long" if int(_safe_float(bar.get("tr_direction_bias", 0.0))) >= 0 else "short"
        setup = TightRangeSetup(
            valid=valid,
            upper=upper if np.isfinite(upper) else close,
            lower=lower if np.isfinite(lower) else close,
            range_atr=_safe_float(bar.get("tr_range_atr", np.nan)),
            count=int(_safe_float(bar.get("tr_count", 0.0))),
            direction_bias=1 if side == "long" else -1,
        )
        trig = resolve_breakout_trigger(setup, bar, tick_size=tick)
        if trig is None:
            out.append(
                {
                    "side": side,
                    "order_type": "stop",
                    "trigger": np.nan,
                    "filtered_reason": "invalid_trigger",
                }
            )
            return out
        reasons: list[str] = []
        if not _safe_bool(bar.get("breakout_pass", False)):
            reasons.append("quality_gate")
        if not _side_allowed(mode, str(trig["side"]).lower()):
            reasons.append("side_mode")
        out.append(
            {
                "side": str(trig["side"]).lower(),
                "order_type": "stop",
                "trigger": _safe_float(trig.get("trigger", np.nan)),
                "filtered_reason": ",".join(reasons) if reasons else None,
            }
        )
        return out

    if signal_type == "breakout_pullback_continuation":
        if not _safe_bool(bar.get("bp_valid", False)):
            return out
        side = str(bar.get("bp_direction", "")).strip().lower()
        if side not in {"long", "short"}:
            return out
        setup = PullbackSetup(
            valid=_safe_bool(bar.get("bp_valid", False)),
            direction=side,  # type: ignore[arg-type]
            breakout_level=_safe_float(bar.get("bp_breakout_level", np.nan)),
            pullback_low=_safe_float(bar.get("bp_pullback_low", np.nan)),
            bars_since_breakout=int(_safe_float(bar.get("bp_bars_since_breakout", 0.0))),
            confirmed=_safe_bool(bar.get("bp_confirmed", False)),
        )
        trig = pullback_entry_trigger(setup, bar, tick_size=tick)
        reasons: list[str] = []
        trigger = np.nan
        if not _safe_bool(bar.get("bp_confirmed", False)):
            reasons.append("not_confirmed")
        elif trig is None:
            reasons.append("invalid_trigger")
        else:
            side = str(trig.get("side", side)).strip().lower()
            trigger = _safe_float(trig.get("trigger", np.nan))
        if not _side_allowed(mode, side):
            reasons.append("side_mode")
        out.append(
            {
                "side": side,
                "order_type": "stop",
                "trigger": trigger,
                "filtered_reason": ",".join(reasons) if reasons else None,
            }
        )
        return out

    return out


def generate_candidate_opportunities(
    frame: pd.DataFrame,
    symbol: str,
    exchange: str,
    interval: str,
    signal_type: str,
    horizon_bars: int = 20,
    trade_side_mode: str = "both",
    # P0.5 修正：0.001 (0.1%) 过严，开仓后 1-2 根 bar 即触发止损；
    # 1% 在 CTA 实战是常见 ATR 一倍止损的合理近似（按品种 ATR 调整）。
    label_stop_loss_pct: float = 0.01,
    feature_columns: tuple[str, ...] = TRAINING_FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Generate candidate opportunities from baseline signal logic.

    说明：
    1. 逐 bar 扫描 baseline 原始 setup，输出 filled + 未成交 + 被过滤样本。
    2. 对 stop 单按“下一根 K 线是否触发”判定，未触发记为负样本。
    3. filled 样本按“入场后逐 bar 跟踪止损”的执行路径计算标签，避免先止损后反弹被误标。
    """
    cols = [
        "symbol",
        "exchange",
        "interval",
        "datetime",
        "signal_datetime",
        "exit_datetime",
        "signal_type",
        "side",
        "order_type",
        "signal_i",
        "entry_i",
        "horizon_i",
        "entry_price",
        "exit_price_ref",
        "stop_price",
        "trigger",
        "future_mfe_atr",
        "future_mae_atr",
        "future_pnl_atr",
        "atr_warmed",
        "label_class",
        "regime_label",
        "candidate_status",
        "is_executed",
        "is_filtered",
        "is_triggered",
        "filtered_reason",
    ] + [f"feature_{c}" for c in feature_columns]
    if frame.empty:
        return pd.DataFrame(columns=cols)

    st = str(signal_type).strip().lower()
    if st not in BASELINE_SIGNAL_TYPES:
        raise ValueError(f"unsupported signal_type={signal_type}, valid={BASELINE_SIGNAL_TYPES}")

    mode = str(trade_side_mode).strip().lower()
    if mode not in VALID_SIDE_MODES:
        raise ValueError(f"invalid trade_side_mode={trade_side_mode}, valid={sorted(VALID_SIDE_MODES)}")

    contract = build_contract_spec(str(symbol).upper(), str(exchange).upper())
    strategy = create_baseline_strategy(
        signal_type=st,
        frame=frame,
        contract=contract,
        trade_side_mode=mode,
    )
    dt = pd.to_datetime(frame.get("datetime", pd.Series([pd.NaT] * len(frame))), errors="coerce")
    hz = max(2, int(horizon_bars))

    rows: list[dict[str, Any]] = []
    for i in range(1, len(frame) - 1):
        bar = frame.iloc[i]
        entry_i = i + 1
        if entry_i >= len(frame):
            continue
        entry_bar = frame.iloc[entry_i]
        horizon_i = min(len(frame) - 1, entry_i + hz)
        if horizon_i <= entry_i:
            continue

        try:
            live_orders = strategy.on_bar(i, bar, position=0)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.debug("candidate scan skipped i=%s signal=%s due to: %s", i, st, exc)
            live_orders = []
        order_by_side: dict[str, dict[str, Any]] = {}
        for od in live_orders:
            side = str(od.get("side", "")).strip().lower()
            if side in {"long", "short"}:
                order_by_side[side] = od

        raw_setups = _build_raw_setup_candidates(frame, i, signal_type=st, contract=contract, mode=mode)
        if not raw_setups and order_by_side:
            for side, od in order_by_side.items():
                raw_setups.append(
                    {
                        "side": side,
                        "order_type": str(od.get("order_type", "market")).strip().lower(),
                        "trigger": _safe_float(od.get("price", np.nan)),
                        "filtered_reason": None,
                    }
                )
        for setup in raw_setups:
            side = str(setup.get("side", "")).strip().lower()
            order_type = str(setup.get("order_type", "stop")).strip().lower()
            trigger = _safe_float(setup.get("trigger", np.nan))
            filtered_reason = setup.get("filtered_reason")
            order = order_by_side.get(side)
            if order is None:
                order = {"side": side, "order_type": order_type, "price": trigger}

            if filtered_reason is not None:
                triggered = False
                entry_price = trigger if np.isfinite(trigger) else np.nan
                stop_price = trigger if order_type == "stop" and np.isfinite(trigger) else np.nan
                candidate_status = "filtered"
            else:
                triggered, entry_price, stop_price = _resolve_candidate_entry(order, entry_bar)
                candidate_status = "filled" if triggered and np.isfinite(entry_price) else "not_triggered"

            # ATR 归一化：优先用 entry_bar 的 atr14（与实盘风险预算口径一致），
            # 若 entry_bar atr14 缺失则回退 signal bar，再失败则用 high-low 兜底；
            # 同时记录 atr_warmed 标志，下游模型可据此过滤前 ~14 根 warmup 样本。
            atr_entry = _safe_float(entry_bar.get("atr14", np.nan))
            atr_signal = _safe_float(bar.get("atr14", np.nan))
            if np.isfinite(atr_entry) and atr_entry > 0:
                atr_v = atr_entry
                atr_warmed = True
            elif np.isfinite(atr_signal) and atr_signal > 0:
                atr_v = atr_signal
                atr_warmed = True
            else:
                hi_lo = _safe_float(entry_bar.get("high", np.nan)) - _safe_float(
                    entry_bar.get("low", np.nan)
                )
                atr_v = hi_lo if np.isfinite(hi_lo) and hi_lo > 0 else np.nan
                atr_warmed = False

            if candidate_status == "filled":
                sim = _simulate_candidate_execution_path(
                    frame,
                    entry_i=entry_i,
                    horizon_i=horizon_i,
                    side=side,
                    entry_price=float(entry_price),
                    atr_v=float(atr_v),
                    stop_loss_pct=float(label_stop_loss_pct),
                )
                mfe_atr = _safe_float(sim.get("mfe_atr", np.nan))
                mae_atr = _safe_float(sim.get("mae_atr", np.nan))
                future_pnl_atr = _safe_float(sim.get("pnl_atr", np.nan))
                exit_close = _safe_float(sim.get("exit_price", np.nan))
                future_pnl = future_pnl_atr * atr_v if np.isfinite(future_pnl_atr) and np.isfinite(atr_v) else np.nan
                # P0.5 修正：恢复 LABEL_THRESHOLD 阈值，过滤"边际净 PnL"过低的"勉强赢家"。
                # 优先用 (mfe - 0.7*mae) 口径与 OOT evaluation 保持一致；
                # 仅在 ATR 缺失时退回到 PnL 正负判定。
                if atr_warmed and np.isfinite(mfe_atr) and np.isfinite(mae_atr):
                    edge = float(mfe_atr) - LABEL_MAE_PENALTY * float(mae_atr)
                    label_class = int(edge > LABEL_THRESHOLD)
                elif atr_warmed and np.isfinite(future_pnl_atr):
                    label_class = int(future_pnl_atr > LABEL_THRESHOLD)
                elif np.isfinite(future_pnl):
                    label_class = int(future_pnl > 0.0)
                else:
                    label_class = 0
            else:
                # P0.5: not_triggered / filtered 样本也按"虚拟入场 + 同一止损规则"做执行路径
                # 模拟，与 filled 样本的 label 口径一致；避免一类样本反映真实执行、
                # 另一类样本反映理想端点导致 train 集口径割裂。
                # 虚拟入场价兜底顺序：trigger（触发价）→ entry_price → entry_bar 开盘价。
                # ⚠️ 不能用 stop_price 兜底，stop_price 在 limit-order/ATR breakout
                # 模式下与 trigger 不同（可能远离触发线），错用会污染未来标签。
                hypo_entry = (
                    trigger
                    if np.isfinite(trigger)
                    else (
                        entry_price
                        if np.isfinite(entry_price)
                        else _safe_float(entry_bar.get("open", np.nan))
                    )
                )
                if np.isfinite(hypo_entry):
                    sim = _simulate_candidate_execution_path(
                        frame,
                        entry_i=entry_i,
                        horizon_i=horizon_i,
                        side=side,
                        entry_price=float(hypo_entry),
                        atr_v=float(atr_v),
                        stop_loss_pct=float(label_stop_loss_pct),
                    )
                    mfe_atr = _safe_float(sim.get("mfe_atr", np.nan))
                    mae_atr = _safe_float(sim.get("mae_atr", np.nan))
                    future_pnl_atr = _safe_float(sim.get("pnl_atr", np.nan))
                    exit_close = _safe_float(sim.get("exit_price", np.nan))
                    future_pnl = (
                        future_pnl_atr * atr_v
                        if np.isfinite(future_pnl_atr) and np.isfinite(atr_v)
                        else np.nan
                    )
                else:
                    mfe_atr = np.nan
                    mae_atr = np.nan
                    future_pnl = np.nan
                    future_pnl_atr = np.nan
                    exit_close = _safe_float(
                        frame.iloc[horizon_i].get("close", np.nan)
                    ) if horizon_i < len(frame) else np.nan
                # not_triggered / filtered 的 label_class 保留 0：
                # 这些是"市场未触发"或"被规则过滤"的样本，本质属于"未发生交易"，
                # 应该作为负样本，不该因为虚拟执行有理论盈利就反向贴金。
                label_class = 0

            row: dict[str, Any] = {
                "symbol": str(symbol).upper(),
                "exchange": str(exchange).upper(),
                "interval": str(interval),
                "datetime": dt.iloc[entry_i] if entry_i < len(dt) else pd.NaT,
                "signal_datetime": dt.iloc[i],
                "exit_datetime": dt.iloc[horizon_i] if horizon_i < len(dt) else pd.NaT,
                "signal_type": st,
                "side": side,
                "order_type": order_type,
                "signal_i": i,
                "entry_i": entry_i,
                "horizon_i": horizon_i,
                "entry_price": float(entry_price) if np.isfinite(entry_price) else np.nan,
                "exit_price_ref": float(exit_close) if np.isfinite(exit_close) else np.nan,
                "stop_price": float(stop_price) if np.isfinite(stop_price) else np.nan,
                # 触发价单独写出，下游 candidate_training_dataset 用作 entry_price_virtual 兜底。
                "trigger": float(trigger) if np.isfinite(trigger) else np.nan,
                # ⚠️ 历史上这里写 0.0，会让"未知机会"被混进"差机会"，下游模型学到
                # NaN==差。改为保留 NaN 让下游显式处理 atr_warmup / 缺价场景。
                "future_mfe_atr": float(mfe_atr) if np.isfinite(mfe_atr) else np.nan,
                "future_mae_atr": float(mae_atr) if np.isfinite(mae_atr) else np.nan,
                # 真实 horizon 收益（除以 atr），与 mfe-0.7*mae 的 opportunity score 区分。
                "future_pnl_atr": float(future_pnl_atr) if np.isfinite(future_pnl_atr) else np.nan,
                "atr_warmed": int(bool(atr_warmed)),
                "label_class": int(label_class),
                "regime_label": _infer_regime_label(bar),
                "candidate_status": candidate_status,
                "is_executed": int(candidate_status == "filled"),
                "is_filtered": int(candidate_status == "filtered"),
                "is_triggered": int(triggered),
                "filtered_reason": str(filtered_reason) if filtered_reason is not None else "",
            }
            for c in feature_columns:
                row[f"feature_{c}"] = bar[c] if c in frame.columns else np.nan
            rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out.dropna(subset=["datetime"]).sort_values(["datetime", "signal_type", "side"]).reset_index(drop=True)
    return out


def _compute_metrics(trade_log: pd.DataFrame, equity_curve: pd.Series, initial_capital: float, periods_per_year: int) -> dict[str, float]:
    s = summarize_trades(trade_log, equity_curve, periods_per_year=periods_per_year)
    total_pnl = float(s["total_pnl"])
    return {
        "total_pnl": total_pnl,
        "total_return": total_pnl / float(initial_capital) if initial_capital else 0.0,
        "annualized": float(s["annualized"]),
        "mdd": float(s["mdd"]),
        "sharpe": float(s["sharpe"]),
        "calmar": float(s["calmar"]),
        "winrate": float(s["winrate"]),
        "pf": float(s["pf"]),
        "trade_count": int(s["trade_count"]),
    }


def run_baseline_suite(
    symbol: str,
    exchange: str | None,
    interval: str,
    start_date: str,
    end_date: str,
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    trade_side_mode: str = "both",
    initial_capital: float = 1_000_000.0,
    periods_per_year: int | None = None,
    output_root: Path | None = None,
) -> BaselineSuiteRunResult:
    """Run baseline suite and export summary + training samples."""
    interval_norm = normalize_interval(interval)
    mode = str(trade_side_mode).strip().lower()
    if mode not in VALID_SIDE_MODES:
        raise ValueError(f"invalid trade_side_mode={trade_side_mode}, valid={sorted(VALID_SIDE_MODES)}")
    strategies = tuple(str(s).strip().lower() for s in signal_types)
    for st in strategies:
        if st not in BASELINE_SIGNAL_TYPES:
            raise ValueError(f"unsupported signal_type={st}, valid={BASELINE_SIGNAL_TYPES}")

    ppy = int(periods_per_year) if periods_per_year is not None else suggest_periods_per_year(interval_norm)
    bcfg = BacktestConfig(
        initial_capital=float(initial_capital),
        periods_per_year=ppy,
        interval=interval_norm,
    )
    sym = str(symbol).upper()
    ex = str(exchange).upper() if exchange else resolve_exchange(sym, bcfg.symbols_list_path)
    bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
    frame = prepare_master_feature_frame(bars, interval=interval_norm)
    contract = build_contract_spec(sym, ex)

    run_date = pd.Timestamp.now().strftime("%Y%m%d")
    root = output_root or bcfg.output_root
    out_dir = root / f"{run_date}_baseline_skill_suite_{sym}_{interval_norm}_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    sample_parts: list[pd.DataFrame] = []
    for st in strategies:
        strat = create_baseline_strategy(
            signal_type=st,
            frame=frame,
            contract=contract,
            trade_side_mode=mode,
        )
        engine_cfg = EngineConfig(
            fill_rule="next_open",
            stop_fill="worst",
            cost_fn=estimate_cost,
            slippage_ticks=contract.slippage_ticks,
        )
        out = run_backtest(frame, strat, engine_cfg)
        trade_log = out["trade_log"].copy()
        equity_curve = (out["equity_curve"].astype(float) + float(initial_capital)).rename("equity")
        metrics = _compute_metrics(trade_log, equity_curve, float(initial_capital), ppy)

        strat_dir = out_dir / st
        strat_dir.mkdir(parents=True, exist_ok=True)
        trades_path = strat_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_{st}_trades.csv"
        equity_path = strat_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_{st}_equity.csv"
        summary_path = strat_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_{st}_summary.csv"
        trade_log.to_csv(trades_path, index=False, encoding="utf-8-sig")
        pd.DataFrame({"equity": equity_curve}).to_csv(equity_path, index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [{**metrics, "symbol": sym, "exchange": ex, "interval": interval_norm, "signal_type": st}]
        ).to_csv(summary_path, index=False, encoding="utf-8-sig")

        summary_rows.append(
            {
                "symbol": sym,
                "exchange": ex,
                "interval": interval_norm,
                "trade_side_mode": mode,
                "signal_type": st,
                **metrics,
                "trades_path": str(trades_path),
                "equity_path": str(equity_path),
                "summary_path": str(summary_path),
            }
        )
        candidates = generate_candidate_opportunities(
            frame=frame,
            symbol=sym,
            exchange=ex,
            interval=interval_norm,
            signal_type=st,
            horizon_bars=20,
            trade_side_mode=mode,
        )
        if candidates.empty:
            # fallback: keep compatibility when candidate scan is empty
            samples = build_training_samples_from_trade_log(
                trade_log=trade_log,
                frame=frame,
                symbol=sym,
                exchange=ex,
                interval=interval_norm,
                signal_type=st,
            )
            if not samples.empty:
                samples = samples.copy()
                samples["label_class"] = samples["label_win"].astype(int)
                samples["future_mfe_atr"] = samples["label_mfe_atr"].astype(float)
                samples["future_mae_atr"] = samples["label_mae_atr"].astype(float)
                trend_dir = pd.to_numeric(samples.get("feature_trend_dir", 0), errors="coerce").fillna(0.0)
                samples["regime_label"] = np.where(
                    trend_dir > 0,
                    "trend_up",
                    np.where(trend_dir < 0, "trend_down", "range"),
                )
                sample_parts.append(samples)
        else:
            sample_parts.append(candidates)

    suite_summary = pd.DataFrame(summary_rows)
    suite_summary_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_suite_summary.csv"
    suite_summary.to_csv(suite_summary_path, index=False, encoding="utf-8-sig")

    if sample_parts:
        training_samples = pd.concat(sample_parts, axis=0, ignore_index=True).sort_values("datetime")
    else:
        training_samples = build_training_samples_from_trade_log(
            trade_log=pd.DataFrame(),
            frame=frame,
            symbol=sym,
            exchange=ex,
            interval=interval_norm,
            signal_type="none",
        )
    training_samples_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_training_samples.csv"
    training_samples.to_csv(training_samples_path, index=False, encoding="utf-8-sig")

    report_path = out_dir / f"{run_date}_{sym}_{interval_norm}_{mode}_baseline_report.md"
    summary_view = suite_summary[
        [
            "signal_type",
            "total_return",
            "annualized",
            "mdd",
            "sharpe",
            "calmar",
            "winrate",
            "pf",
            "trade_count",
        ]
    ]
    try:
        summary_block = summary_view.to_markdown(index=False, floatfmt=".6f")
    except (ImportError, ValueError) as exc:
        # tabulate 缺失或其它 markdown 转换问题：回退到等宽文本，确保报告仍可生成。
        logger.warning("to_markdown failed (%s), fallback to to_string", exc)
        summary_block = "```\n" + summary_view.to_string(index=False, float_format="%.6f") + "\n```"

    lines = [
        "# Baseline Skill Suite Report",
        "",
        f"- symbol: `{sym}.{ex}`",
        f"- interval: `{interval_norm}`",
        f"- trade_side_mode: `{mode}`",
        f"- range: `{start_date}` -> `{end_date}`",
        f"- bars: `{len(frame)}`",
        f"- strategies: `{', '.join(strategies)}`",
        "",
        "## Summary",
        summary_block,
        "",
        f"- suite_summary_csv: `{suite_summary_path}`",
        f"- training_samples_csv: `{training_samples_path}`",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info("baseline suite done: %s", out_dir)
    return BaselineSuiteRunResult(
        output_dir=out_dir,
        summary_path=suite_summary_path,
        training_samples_path=training_samples_path,
        report_path=report_path,
    )


def run_baseline_suite_multi(
    symbol: str,
    exchange: str | None,
    intervals: Sequence[str] | str,
    start_date: str,
    end_date: str,
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    trade_side_mode: str = "both",
    initial_capital: float = 1_000_000.0,
    periods_per_year: int | None = None,
    output_root: Path | None = None,
) -> list[BaselineSuiteRunResult]:
    """Run baseline suite across multiple intervals.

    单个 interval 失败不会阻塞其它 interval，便于批量研究。
    """
    interval_tuple = _normalize_intervals(intervals)
    results: list[BaselineSuiteRunResult] = []
    for idx, interval in enumerate(interval_tuple, start=1):
        logger.info(
            "[%d/%d] baseline suite symbol=%s interval=%s",
            idx,
            len(interval_tuple),
            str(symbol).upper(),
            interval,
        )
        try:
            res = run_baseline_suite(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                signal_types=signal_types,
                trade_side_mode=trade_side_mode,
                initial_capital=initial_capital,
                periods_per_year=periods_per_year,
                output_root=output_root,
            )
        except Exception:
            logger.exception("baseline suite failed for symbol=%s interval=%s", symbol, interval)
            continue
        results.append(res)
    return results


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run baseline skill suite and build training samples")
    parser.add_argument("--symbol", default="RB0")
    parser.add_argument("--exchange", default=None)
    parser.add_argument(
        "--top-n-symbols",
        type=int,
        default=0,
        help=(
            "if > 0, ignore --symbol and load top-N symbols from --symbols-ranking-path "
            "ordered by research_rank"
        ),
    )
    parser.add_argument(
        "--symbols-ranking-path",
        default=str(SYMBOLS_RANKING_PATH),
        help="csv path of symbol research ranking (default cta/feature/symbols_research_ranking.csv)",
    )
    parser.add_argument(
        "--interval",
        nargs="+",
        default=["60min"],
        help=(
            "one or more intervals (day/60min/30min/15min/5min/min). "
            "Accepts space-separated and comma-separated tokens; duplicates are deduped."
        ),
    )
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--end", default="2019-12-31")
    parser.add_argument("--trade-side-mode", default="both", choices=sorted(VALID_SIDE_MODES))
    parser.add_argument("--signal-types", default=",".join(BASELINE_SIGNAL_TYPES))
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--periods-per-year", type=int, default=None)
    parser.add_argument("--output-root", default=None)
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _parse_args()
    signal_types = tuple(s.strip() for s in str(args.signal_types).split(",") if s.strip())
    intervals = _normalize_intervals(args.interval)
    output_root = Path(args.output_root).resolve() if args.output_root else None

    top_n = int(getattr(args, "top_n_symbols", 0))
    if top_n > 0:
        symbols_to_run = _load_top_n_symbols_from_ranking(
            Path(args.symbols_ranking_path),
            top_n=top_n,
        )
        logger.info(
            "top-n symbol mode enabled: top_n=%s ranking_path=%s loaded=%s",
            top_n,
            args.symbols_ranking_path,
            [s for s, _ in symbols_to_run],
        )
    else:
        symbols_to_run = [(str(args.symbol).upper(), str(args.exchange).upper() if args.exchange else None)]

    all_results: list[BaselineSuiteRunResult] = []
    for sidx, (symbol, exchange_from_rank) in enumerate(symbols_to_run, start=1):
        run_exchange = _resolve_run_exchange(exchange_from_rank, args.exchange)
        logger.info(
            "[%d/%d] run symbol=%s exchange=%s intervals=%s",
            sidx,
            len(symbols_to_run),
            symbol,
            run_exchange,
            list(intervals),
        )
        results = run_baseline_suite_multi(
            symbol=symbol,
            exchange=run_exchange,
            intervals=intervals,
            start_date=args.start,
            end_date=args.end,
            signal_types=signal_types,
            trade_side_mode=args.trade_side_mode,
            initial_capital=args.initial_capital,
            periods_per_year=args.periods_per_year,
            output_root=output_root,
        )
        all_results.extend(results)
        for result in results:
            logger.info("[%s] summary: %s", symbol, result.summary_path)
            logger.info("[%s] training_samples: %s", symbol, result.training_samples_path)
            logger.info("[%s] report: %s", symbol, result.report_path)
        if len(results) < len(intervals):
            logger.warning(
                "[%s] only %d/%d intervals succeeded; see logs for failures",
                symbol,
                len(results),
                len(intervals),
            )

    if not all_results:
        raise SystemExit("no baseline suite runs succeeded")


if __name__ == "__main__":
    main()


__all__ = [
    "BASELINE_SIGNAL_TYPES",
    "BaselineSuiteRunResult",
    "prepare_master_feature_frame",
    "create_baseline_strategy",
    "generate_candidate_opportunities",
    "build_training_samples_from_trade_log",
    "run_baseline_suite",
    "run_baseline_suite_multi",
]
