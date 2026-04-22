"""Brooks v3 参数加载。

所有运行期可调项集中在 config/strategy.yaml;本模块把 yaml 反序列化为若干 dataclass,
便于类型检查、IDE 提示、单元测试构造。CLI 允许字段级覆盖(见 backtest/runner.py)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
BROOKS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = BROOKS_ROOT / "config" / "strategy.yaml"


@dataclass
class SymbolsCfg:
    ranking_csv: str
    top_n: int = 8
    max_tier: str = "B"
    require_feature_interval: str = "minute5"
    explicit_include: list[str] = field(default_factory=list)
    explicit_exclude: list[str] = field(default_factory=list)


@dataclass
class IntervalsCfg:
    htf: str = "day"
    mtf: str = "minute60"
    ltf: str = "minute5"
    warmup_bars: dict[str, int] = field(default_factory=dict)


@dataclass
class HtfSignalCfg:
    always_in_dir_min_abs: float = 0.10
    trend_strength_min_abs: float = 3.0
    ema_slope_must_match: bool = True


@dataclass
class MtfSignalCfg:
    h123_valid: list[int] = field(default_factory=lambda: [1, 2, 3])
    pullback_depth_min: float = 0.15
    pullback_depth_max: float = 0.85
    align_htf_direction: bool = True


@dataclass
class LtfSignalCfg:
    require_breakout_fire: bool = True
    lookback: int = 20
    breakout_strength_min_abs: float = 0.2
    signal_strength_min_abs: float = 1.0
    expected_rr_min: float = 1.2
    reject_if_breakout_fail: bool = True


@dataclass
class SignalCfg:
    htf: HtfSignalCfg = field(default_factory=HtfSignalCfg)
    mtf: MtfSignalCfg = field(default_factory=MtfSignalCfg)
    ltf: LtfSignalCfg = field(default_factory=LtfSignalCfg)


@dataclass
class PortfolioRiskCfg:
    dd_threshold_half: float = 0.02
    dd_threshold_quarter: float = 0.05
    recover_to_full_at_new_high: bool = True


@dataclass
class RiskCfg:
    per_trade_risk_pct: float = 0.001
    atr_window: int = 14
    stop_atr_mult: float = 1.0
    trailing_atr_mult: float = 2.0
    fail_exit_bars: int = 3
    max_holding_bars: int = 30
    portfolio: PortfolioRiskCfg = field(default_factory=PortfolioRiskCfg)


@dataclass
class XgbCfg:
    n_estimators: int = 400
    max_depth: int = 5
    learning_rate: float = 0.05
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    min_child_weight: float = 5.0
    reg_lambda: float = 1.0
    early_stopping_rounds: int = 30
    eval_metric: str = "auc"


@dataclass
class ModelSplitsCfg:
    train_end: str = "2022-06-30"
    val_end: str = "2023-06-30"


@dataclass
class ModelTrainCfg:
    splits: ModelSplitsCfg = field(default_factory=ModelSplitsCfg)
    xgb: XgbCfg = field(default_factory=XgbCfg)


@dataclass
class ModelCfg:
    enabled: bool = True
    path: str | None = None
    threshold: float = 0.55
    target_rr: float = 2.0
    target_bars: int = 20
    train: ModelTrainCfg = field(default_factory=ModelTrainCfg)


@dataclass
class ContractDefaultsCfg:
    default_rate: float = 0.0001
    default_slippage: float = 1.0
    default_size: float = 10.0
    default_pricetick: float = 1.0


@dataclass
class OutputCfg:
    report_root: str = "cta/strategy/brooks/report"
    models_root: str = "cta/strategy/brooks/models"


@dataclass
class BrooksV3Params:
    symbols: SymbolsCfg
    intervals: IntervalsCfg
    signal: SignalCfg
    risk: RiskCfg
    model: ModelCfg
    contract: ContractDefaultsCfg
    output: OutputCfg

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrooksV3Params":
        return cls(
            symbols=SymbolsCfg(**data["symbols"]),
            intervals=IntervalsCfg(**data.get("intervals", {})),
            signal=_parse_signal(data.get("signal", {})),
            risk=_parse_risk(data.get("risk", {})),
            model=_parse_model(data.get("model", {})),
            contract=ContractDefaultsCfg(**data.get("contract", {})),
            output=OutputCfg(**data.get("output", {})),
        )


def _parse_signal(d: dict[str, Any]) -> SignalCfg:
    return SignalCfg(
        htf=HtfSignalCfg(**d.get("htf", {})),
        mtf=MtfSignalCfg(**d.get("mtf", {})),
        ltf=LtfSignalCfg(**d.get("ltf", {})),
    )


def _parse_risk(d: dict[str, Any]) -> RiskCfg:
    portfolio = PortfolioRiskCfg(**d.pop("portfolio", {})) if isinstance(d, dict) else PortfolioRiskCfg()
    return RiskCfg(portfolio=portfolio, **d)


def _parse_model(d: dict[str, Any]) -> ModelCfg:
    train_d = d.pop("train", {}) if isinstance(d, dict) else {}
    splits = ModelSplitsCfg(**train_d.pop("splits", {})) if isinstance(train_d, dict) else ModelSplitsCfg()
    xgb = XgbCfg(**train_d.pop("xgb", {})) if isinstance(train_d, dict) else XgbCfg()
    train = ModelTrainCfg(splits=splits, xgb=xgb)
    return ModelCfg(train=train, **d)


def load_params(yaml_path: str | Path | None = None) -> BrooksV3Params:
    """从 yaml 加载参数。默认读 config/strategy.yaml。"""
    path = Path(yaml_path) if yaml_path else DEFAULT_YAML
    if not path.exists():
        raise FileNotFoundError(f"参数 yaml 不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    logger.info("loaded brooks v3 params from %s", path)
    return BrooksV3Params.from_dict(data)


DEFAULT_PARAMS_PATH = DEFAULT_YAML


__all__ = [
    "BrooksV3Params",
    "SymbolsCfg", "IntervalsCfg",
    "SignalCfg", "HtfSignalCfg", "MtfSignalCfg", "LtfSignalCfg",
    "RiskCfg", "PortfolioRiskCfg",
    "ModelCfg", "ModelTrainCfg", "ModelSplitsCfg", "XgbCfg",
    "ContractDefaultsCfg", "OutputCfg",
    "load_params", "DEFAULT_PARAMS_PATH",
]
