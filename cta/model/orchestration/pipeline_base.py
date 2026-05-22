'CTA model pipeline with signal-type split and walk-forward evaluation.'

from __future__ import annotations

import argparse

import hashlib

import json

import logging

import platform

import re

import shutil

import subprocess

import sys

from dataclasses import dataclass, replace as dc_replace

from functools import lru_cache

from pathlib import Path

from typing import Any, Iterable, Literal, Sequence

import numpy as np

import pandas as pd

from sklearn.model_selection import TimeSeriesSplit

from cta.config.baseline_skill_suite_config import BASELINE_SIGNAL_TYPES, DEFAULT_REPORT_ROOT, LABEL_MAE_PENALTY, LABEL_THRESHOLD

from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG, OotEvaluationConfig

from cta.config.symbol_cluster_config import infer_symbol_cluster

from cta.config.skill_tight_range_breakout_config import BacktestConfig

from cta.model.feature.training_feature_builder import DEFAULT_GENERIC_COLUMNS, FEATURE_ROOT

from cta.model.training.final_decision_model import FinalDecisionModel, evaluate_final_decision_model

from cta.model.training.mfe_mae_model import MfeMaeModel, evaluate_mfe_mae_model

from cta.model.dataset.pipeline_feature_enrichment import _auto_enrich_candidate_features_for_models, _build_training_feature_table_with_auto_fallback

from cta.model.reporting.pipeline_html_report import write_pipeline_oot_html_report

from cta.model.oot.pipeline_oot_evaluation import _evaluate_oot_real_execution

from cta.model.dataset.pipeline_pooling import _build_pooled_feature_df as _build_pooled_feature_df_impl

from cta.model.training.regime_classifier_model import RegimeClassifierModel, evaluate_regime_model

from cta.model.training.trade_filter_model import TradeFilterModel, evaluate_trade_filter_model

from cta.portfolio_logic.score_calibrator import ScoreCalibrator

from cta.strategy.baseline_skill_suite import generate_candidate_opportunities, prepare_master_feature_frame

from cta.strategy.skill_tight_range_backtest import load_bars, normalize_interval, resolve_exchange

from cta.utils.random_seed import seed_all

WindowMode = Literal['expanding', 'sliding', 'rolling']

MFE_MAE_KIND_SKIPPED_NO_EXEC = 'skipped_no_exec'

logger = logging.getLogger(__name__)

_CTA_ROOT = Path(__file__).resolve().parents[2]

FEATURES_DOC_PATH = _CTA_ROOT / 'feature' / 'FEATURES.md'

CAUSALITY_MANIFEST_PATH = _CTA_ROOT / 'feature' / 'causality_manifest.csv'

SYMBOLS_RANKING_PATH = _CTA_ROOT / 'feature' / 'symbols_research_ranking.csv'

_FEATURE_MEANING_FALLBACK: dict[str, str] = {'feature_close': '信号时点收盘价', 'feature_open': '信号时点开盘价', 'feature_high': '信号时点最高价', 'feature_low': '信号时点最低价', 'feature_volume': '信号时点成交量', 'feature_open_interest': '信号时点持仓量', 'feature_turnover': '信号时点成交额', 'feature_atr14': '14周期 ATR 波动率', 'feature_trend_score': '趋势强弱分数', 'feature_trend_dir': '趋势方向编码', 'feature_breakout_score': '突破质量分数', 'feature_setup_quality': '交易 setup 质量分数', 'feature_tr_width': 'tight range 宽度', 'feature_tr_range_atr': 'tight range / ATR 比值', 'feature_don_upper_entry': 'Donchian 上轨入场价', 'feature_don_lower_entry': 'Donchian 下轨入场价', 'feature_don_atr20': 'Donchian 宽度/ATR20', 'feature_bp_trigger': '突破回踩触发价', 'feature_bp_stop': '突破回踩止损价', 'feature_bp_valid': '突破回踩有效标记', 'feature_bp_confirmed': '突破回踩确认标记', 'feature_side_code': '方向编码（long=1, short=-1）', 'feature_signal_code': '信号类型编码', 'feature_trigger': '突破/触发价（决策时刻可见）', 'feature_fallback': '兜底常数特征（无可用特征时）', 'model_trade_setup': '候选构造的交易质量特征（trade gate）', 'model_trade_breakout_trend': '候选构造的突破*趋势交互特征（trade gate）', 'model_regime_state': '候选构造的状态特征（regime gate）', 'model_regime_volatility': '候选构造的波动特征（regime gate）', 'model_mfe_edge': '候选构造的预期边际特征（mfe/mae gate）', 'model_mfe_side_interaction': '候选构造的边际-方向交互特征（mfe/mae gate）', 'auto_close': '候选构造的通用收盘价特征', 'auto_open': '候选构造的通用开盘价特征', 'auto_high': '候选构造的通用最高价特征', 'auto_low': '候选构造的通用最低价特征', 'auto_volume': '候选构造的通用成交量特征'}

GenericMode = Literal['auto', 'whitelist']

@dataclass(frozen=True)
class ModelPipelineResult:
    output_dir: Path
    candidate_path: Path
    feature_table_path: Path
    prediction_path: Path
    metrics_path: Path
    oot_monthly_path: Path
    oot_summary_path: Path
    oot_trades_path: Path
    html_report_path: Path
    top_feature_importance_path: Path
    report_path: Path

@dataclass(frozen=True)
class _WalkForwardWindow:
    window_id: int
    train: pd.DataFrame
    valid: pd.DataFrame
    test: pd.DataFrame
    train_end: pd.Timestamp
    valid_end: pd.Timestamp
    test_end: pd.Timestamp

def _safe_name(value: str) -> str:
    out = re.sub('[^A-Za-z0-9_]+', '_', str(value).strip().lower())
    return out.strip('_') or 'unknown'


__all__ = [
    name
    for name in globals()
    if not (name.startswith("__") and name.endswith("__"))
]
