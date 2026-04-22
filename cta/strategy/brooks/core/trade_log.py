"""成交日志:TradeRecord + parquet writer。

`TradeRecord` 记录一个完整的 open-close 回合(不是单次成交);
含信号触发 bar 的特征快照、MTF/HTF 上下文、模型概率、止损路径、MFE/MAE、退出原因。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    # 组合键
    vt_symbol: str
    open_ts: pd.Timestamp
    close_ts: pd.Timestamp | None = None
    direction: int = 1                 # 1 long
    qty: int = 0
    # 价格
    entry_price: float = 0.0
    exit_price: float = 0.0
    initial_stop: float = 0.0
    final_stop: float = 0.0
    # 绩效
    pnl: float = 0.0
    mfe: float = 0.0                   # 持仓期最大有利变动(价格)
    mae: float = 0.0                   # 持仓期最大不利变动
    holding_bars: int = 0
    # 入场上下文
    setup_type: str = ""               # h1/h2/h3
    htf_direction: int = 0
    htf_trend_strength: float = 0.0
    mtf_pullback_depth: float = 0.0
    # 模型
    model_prob: float = 1.0
    model_accepted: bool = True
    # 退出
    exit_reason: str = "none"
    # 风控快照
    leverage_mult: float = 1.0
    equity_at_open: float = 0.0
    # 特征快照(JSON 字符串,parquet 友好)
    features_json: str = ""

    def finalize(self, close_ts: pd.Timestamp, exit_price: float,
                 exit_reason: str, contract_size: float) -> None:
        self.close_ts = close_ts
        self.exit_price = exit_price
        self.exit_reason = exit_reason
        self.pnl = (self.exit_price - self.entry_price) * self.direction * self.qty * contract_size


@dataclass
class TradeLogger:
    output_path: Path
    trades: list[TradeRecord] = field(default_factory=list)

    def add(self, t: TradeRecord) -> None:
        self.trades.append(t)

    def write_parquet(self) -> Path:
        if not self.trades:
            logger.warning("TradeLogger: 没有成交可写 %s", self.output_path)
            return self.output_path
        rows = [asdict(t) for t in self.trades]
        df = pd.DataFrame(rows)
        # Timestamp 列规范化
        for col in ("open_ts", "close_ts"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(self.output_path, index=False)
        logger.info("TradeLogger: wrote %d trades to %s", len(df), self.output_path)
        return self.output_path


def dumps_features(features: dict) -> str:
    """把特征 dict 序列化成 parquet 友好的 JSON 字符串(处理 numpy 类型)。"""
    def _default(o):
        try:
            import numpy as np
            if isinstance(o, np.generic):
                return o.item()
        except Exception:
            pass
        raise TypeError(f"unserializable: {type(o)}")
    try:
        return json.dumps(features, default=_default, ensure_ascii=False)
    except Exception as e:
        logger.warning("dumps_features failed: %s", e)
        return "{}"


__all__ = ["TradeRecord", "TradeLogger", "dumps_features"]
