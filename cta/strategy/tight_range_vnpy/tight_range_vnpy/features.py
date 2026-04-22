from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .config import DEFAULT_DATE_COLUMNS, DEFAULT_REQUIRED_COLUMNS

EPS = 1e-12


@dataclass
class FeatureConfig:
    atr_window: int = 14
    ema_fast: int = 20
    ema_slow: int = 60
    setup_lookback: int = 12
    pre_leg_lookback: int = 24
    width_rank_lookback: int = 120
    min_breakout_excess_atr: float = 0.10
    max_range_width_atr: float = 1.80
    min_pre_leg_return_atr: float = 1.20


FEATURE_COLUMNS = [
    "symbol", "exchange", "trade_date", "open", "high", "low", "close", "volume", "atr",
    "range_high", "range_low", "range_width_atr", "range_width_vs_hist_mean", "inside_bar_ratio",
    "overlap_ratio", "avg_body_ratio", "avg_upper_wick_ratio", "avg_lower_wick_ratio",
    "false_break_up_count", "false_break_down_count", "pre_leg_return", "pre_leg_return_atr",
    "pre_leg_slope", "pre_leg_pullback_depth_atr", "retrace_ratio_of_pre_leg", "trend_align_flag",
    "hh_hl_count", "ema_fast_slope", "ema_slow_slope", "distance_to_ema_fast_atr",
    "distance_to_ema_slow_atr", "breakout_flag", "breakout_excess_atr", "breakout_body_ratio",
    "breakout_close_pos", "breakout_volume_ratio", "breakout_gap_from_prev_close_atr",
    "breakout_through_prior_20_high", "breakout_through_prior_60_high",
    "distance_to_prior_20_high_atr", "distance_to_prior_60_high_atr", "measured_move_space_atr",
    "stop_distance_atr", "target_distance_atr", "expected_rr_rule", "breakout_near_major_resistance",
    "candidate_flag", "rule_structure_score", "rule_breakout_score", "rule_context_score",
    "rule_risk_score", "opportunity_score", "opportunity_level"
]


class FeatureBuilder:
    def __init__(self, cfg: Optional[FeatureConfig] = None) -> None:
        self.cfg = cfg or FeatureConfig()

    @staticmethod
    def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
        return a / (b.replace(0, np.nan) + EPS)

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.columns = [str(c).strip().lower() for c in out.columns]
        return out

    def load_csv(self, csv_path: Path | str) -> pd.DataFrame:
        csv_path = Path(csv_path)
        df = pd.read_csv(csv_path)
        df = self._normalize_columns(df)

        date_col = None
        for c in DEFAULT_DATE_COLUMNS:
            if c in df.columns:
                date_col = c
                break
        if date_col is None:
            raise ValueError(f"{csv_path} 缺少日期列，支持: {DEFAULT_DATE_COLUMNS}")

        missing = [c for c in DEFAULT_REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{csv_path} 缺少必要列: {missing}")

        df[date_col] = pd.to_datetime(df[date_col])
        df = df.sort_values(date_col).reset_index(drop=True)

        if "symbol" not in df.columns:
            df["symbol"] = csv_path.stem.upper()
        if "exchange" not in df.columns:
            df["exchange"] = "LOCAL"

        df = df.rename(columns={date_col: "datetime"})
        df["trade_date"] = df["datetime"].dt.strftime("%Y-%m-%d")
        df["symbol"] = df["symbol"].astype(str).str.upper()
        df["exchange"] = df["exchange"].astype(str).str.upper()
        return df

    def calc_true_range(self, df: pd.DataFrame) -> pd.Series:
        prev_close = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    def add_basic_features(self, df: pd.DataFrame) -> pd.DataFrame:
        cfg = self.cfg
        out = df.copy()
        out["tr"] = self.calc_true_range(out)
        out["atr"] = out["tr"].rolling(cfg.atr_window, min_periods=cfg.atr_window).mean()

        out["bar_range"] = out["high"] - out["low"]
        out["body_size"] = (out["close"] - out["open"]).abs()
        out["upper_wick"] = out["high"] - out[["open", "close"]].max(axis=1)
        out["lower_wick"] = out[["open", "close"]].min(axis=1) - out["low"]
        out["body_ratio"] = self._safe_div(out["body_size"], out["bar_range"]).clip(0, 1)
        out["upper_wick_ratio"] = self._safe_div(out["upper_wick"], out["bar_range"]).clip(0, 1)
        out["lower_wick_ratio"] = self._safe_div(out["lower_wick"], out["bar_range"]).clip(0, 1)
        out["close_pos"] = self._safe_div(out["close"] - out["low"], out["bar_range"]).clip(0, 1)

        out["volume_ma_20"] = out["volume"].rolling(20, min_periods=20).mean()
        out["volume_ratio"] = self._safe_div(out["volume"], out["volume_ma_20"])

        out["ema_fast"] = out["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
        out["ema_slow"] = out["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
        out["ema_fast_slope"] = self._safe_div(out["ema_fast"] - out["ema_fast"].shift(5), out["atr"])
        out["ema_slow_slope"] = self._safe_div(out["ema_slow"] - out["ema_slow"].shift(5), out["atr"])
        out["distance_to_ema_fast_atr"] = self._safe_div(out["close"] - out["ema_fast"], out["atr"])
        out["distance_to_ema_slow_atr"] = self._safe_div(out["close"] - out["ema_slow"], out["atr"])
        return out

    def add_tight_range_features(self, df: pd.DataFrame) -> pd.DataFrame:
        cfg = self.cfg
        out = df.copy()
        l1 = cfg.setup_lookback
        l2 = cfg.pre_leg_lookback

        out["range_high"] = out["high"].shift(1).rolling(l1, min_periods=l1).max()
        out["range_low"] = out["low"].shift(1).rolling(l1, min_periods=l1).min()
        out["range_width"] = out["range_high"] - out["range_low"]
        out["range_width_atr"] = self._safe_div(out["range_width"], out["atr"])
        out["range_width_hist_mean"] = out["range_width"].shift(1).rolling(cfg.width_rank_lookback, min_periods=cfg.width_rank_lookback).mean()
        out["range_width_vs_hist_mean"] = self._safe_div(out["range_width"], out["range_width_hist_mean"])

        inside_bar = ((out["high"] <= out["high"].shift(1)) & (out["low"] >= out["low"].shift(1))).astype(int)
        out["inside_bar_ratio"] = inside_bar.shift(1).rolling(l1, min_periods=l1).mean()

        overlap = (
            np.minimum(out["high"], out["high"].shift(1)) -
            np.maximum(out["low"], out["low"].shift(1))
        ).clip(lower=0)
        out["overlap_ratio"] = self._safe_div(overlap, out["bar_range"].shift(1)).shift(1).rolling(l1, min_periods=l1).mean()

        out["avg_body_ratio"] = out["body_ratio"].shift(1).rolling(l1, min_periods=l1).mean()
        out["avg_upper_wick_ratio"] = out["upper_wick_ratio"].shift(1).rolling(l1, min_periods=l1).mean()
        out["avg_lower_wick_ratio"] = out["lower_wick_ratio"].shift(1).rolling(l1, min_periods=l1).mean()

        prev_range_high = out["range_high"]
        prev_range_low = out["range_low"]
        false_break_up = ((out["high"] > prev_range_high) & (out["close"] <= prev_range_high)).astype(int)
        false_break_down = ((out["low"] < prev_range_low) & (out["close"] >= prev_range_low)).astype(int)
        out["false_break_up_count"] = false_break_up.shift(1).rolling(l1, min_periods=l1).sum()
        out["false_break_down_count"] = false_break_down.shift(1).rolling(l1, min_periods=l1).sum()

        out["pre_leg_return"] = self._safe_div(out["close"].shift(1) - out["close"].shift(1 + l2), out["close"].shift(1 + l2))
        out["pre_leg_return_atr"] = self._safe_div(out["close"].shift(1) - out["close"].shift(1 + l2), out["atr"])
        out["pre_leg_slope"] = self._safe_div(out["close"].shift(1) - out["close"].shift(1 + l2), l2 * out["atr"])

        range_mid = (out["range_high"] + out["range_low"]) / 2.0
        recent_peak = out["high"].shift(1).rolling(l2, min_periods=l2).max()
        recent_trough = out["low"].shift(1).rolling(l2, min_periods=l2).min()
        out["pre_leg_pullback_depth_atr"] = self._safe_div(recent_peak - range_mid, out["atr"])
        out["retrace_ratio_of_pre_leg"] = self._safe_div(recent_peak - range_mid, recent_peak - recent_trough)

        out["trend_align_flag"] = (
            (out["ema_fast"] > out["ema_slow"]) &
            (out["ema_fast_slope"] > 0) &
            (out["ema_slow_slope"] > 0)
        ).astype(int)

        hh = (out["high"].shift(1) > out["high"].shift(2)).astype(int)
        hl = (out["low"].shift(1) > out["low"].shift(2)).astype(int)
        out["hh_hl_count"] = (hh + hl).rolling(l2, min_periods=l2).sum()
        return out

    def add_breakout_and_score(self, df: pd.DataFrame) -> pd.DataFrame:
        cfg = self.cfg
        out = df.copy()
        prev_range_high = out["range_high"]
        prev_range_low = out["range_low"]

        out["breakout_flag"] = (out["close"] > prev_range_high).astype(int)
        out["breakout_excess_atr"] = self._safe_div((out["close"] - prev_range_high).clip(lower=0), out["atr"])
        out["breakout_body_ratio"] = out["body_ratio"]
        out["breakout_close_pos"] = out["close_pos"]
        out["breakout_volume_ratio"] = out["volume_ratio"]
        out["breakout_gap_from_prev_close_atr"] = self._safe_div(out["open"] - out["close"].shift(1), out["atr"])

        prior_20_high = out["high"].shift(1).rolling(20, min_periods=20).max()
        prior_60_high = out["high"].shift(1).rolling(60, min_periods=60).max()
        out["breakout_through_prior_20_high"] = (out["close"] > prior_20_high).astype(int)
        out["breakout_through_prior_60_high"] = (out["close"] > prior_60_high).astype(int)
        out["distance_to_prior_20_high_atr"] = self._safe_div(prior_20_high - out["close"], out["atr"])
        out["distance_to_prior_60_high_atr"] = self._safe_div(prior_60_high - out["close"], out["atr"])

        pre_leg_len = (
            out["high"].shift(1).rolling(cfg.pre_leg_lookback, min_periods=cfg.pre_leg_lookback).max() -
            out["low"].shift(1).rolling(cfg.pre_leg_lookback, min_periods=cfg.pre_leg_lookback).min()
        )
        measured_target = prev_range_high + pre_leg_len
        out["measured_move_space_atr"] = self._safe_div(measured_target - out["close"], out["atr"])

        stop_price = prev_range_low - 0.5 * out["atr"]
        target_price = measured_target
        out["stop_distance_atr"] = self._safe_div((out["close"] - stop_price).clip(lower=0), out["atr"])
        out["target_distance_atr"] = self._safe_div((target_price - out["close"]).clip(lower=0), out["atr"])
        out["expected_rr_rule"] = self._safe_div(out["target_distance_atr"], out["stop_distance_atr"])
        out["breakout_near_major_resistance"] = out["distance_to_prior_60_high_atr"].between(-0.3, 0.8, inclusive="both").astype(int)

        out["candidate_flag"] = (
            (out["range_width_atr"] <= cfg.max_range_width_atr) &
            (out["pre_leg_return_atr"] >= cfg.min_pre_leg_return_atr) &
            (out["breakout_flag"] == 1) &
            (out["breakout_excess_atr"] >= cfg.min_breakout_excess_atr)
        ).astype(int)

        structure = (
            40 * (1 - out["range_width_vs_hist_mean"].clip(0, 2) / 2.0).fillna(0) +
            25 * out["inside_bar_ratio"].fillna(0).clip(0, 1) +
            20 * out["overlap_ratio"].fillna(0).clip(0, 1) +
            15 * (1 - out["retrace_ratio_of_pre_leg"].fillna(1).clip(0, 1))
        )
        breakout = (
            35 * out["breakout_body_ratio"].fillna(0).clip(0, 1) +
            35 * out["breakout_close_pos"].fillna(0).clip(0, 1) +
            15 * np.tanh(out["breakout_excess_atr"].fillna(0)) +
            15 * np.tanh((out["breakout_volume_ratio"].fillna(1) - 1).clip(-1, 3))
        )
        context = (
            35 * out["trend_align_flag"].fillna(0) +
            20 * np.tanh(out["pre_leg_return_atr"].fillna(0) / 2.0) +
            20 * np.tanh(out["measured_move_space_atr"].fillna(0) / 2.0) +
            25 * (1 - out["breakout_near_major_resistance"].fillna(0))
        )
        risk = (
            50 * np.tanh(out["expected_rr_rule"].fillna(0) / 3.0) +
            50 * (1 - (out["stop_distance_atr"].fillna(10).clip(0, 5) / 5.0))
        )

        out["rule_structure_score"] = structure.clip(0, 100)
        out["rule_breakout_score"] = breakout.clip(0, 100)
        out["rule_context_score"] = context.clip(0, 100)
        out["rule_risk_score"] = risk.clip(0, 100)

        out["opportunity_score"] = (
            0.30 * out["rule_structure_score"] +
            0.25 * out["rule_breakout_score"] +
            0.25 * out["rule_context_score"] +
            0.20 * out["rule_risk_score"]
        ).where(out["candidate_flag"] == 1, 0.0).clip(0, 100)

        out["opportunity_level"] = np.select(
            [
                out["opportunity_score"] >= 80,
                out["opportunity_score"] >= 68,
                out["opportunity_score"] >= 55,
            ],
            ["A", "B", "C"],
            default="D"
        )
        return out

    def build_from_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self.add_basic_features(df)
        out = self.add_tight_range_features(out)
        out = self.add_breakout_and_score(out)
        out = out[FEATURE_COLUMNS].copy()
        out = out.replace([np.inf, -np.inf], np.nan)
        return out.dropna(subset=["trade_date", "close"]).reset_index(drop=True)

    def build_from_csv(self, csv_path: Path | str) -> pd.DataFrame:
        df = self.load_csv(csv_path)
        return self.build_from_dataframe(df)
