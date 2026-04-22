"""模型评分门控:加载 XGBoost .ubj 模型,对当前 setup 特征打分。

接口:
    gate = ScoreGate.load(path, threshold=0.55)
    prob, accept = gate.score(features_dict)

- features_dict 必须覆盖训练时的 feature_cols(从 .meta.json 读取);缺失列填 0
- 若 model.enabled=False 或 path 为空,返回 (1.0, True) 透传
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ScoreGate:
    model: object | None = None
    feature_cols: list[str] | None = None
    threshold: float = 0.55
    enabled: bool = True

    @classmethod
    def passthrough(cls) -> "ScoreGate":
        return cls(enabled=False)

    @classmethod
    def load(cls, model_path: str | Path, threshold: float = 0.55) -> "ScoreGate":
        """从 .ubj 加载;同目录下应有 .meta.json 提供 feature_cols。"""
        from xgboost import XGBClassifier

        p = Path(model_path)
        if not p.exists():
            raise FileNotFoundError(f"模型不存在: {p}")
        meta_path = p.with_suffix(".meta.json")
        if not meta_path.exists():
            # 也支持 xgb_<ts>.ubj + xgb_<ts>.meta.json 的同名结构
            alt = p.parent / (p.stem + ".meta.json")
            if alt.exists():
                meta_path = alt
        feature_cols: list[str] | None = None
        if meta_path.exists():
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
            feature_cols = list(meta.get("feature_cols", []))
        m = XGBClassifier()
        m.load_model(str(p))
        logger.info("ScoreGate loaded %s threshold=%.3f feats=%d",
                    p, threshold, len(feature_cols or []))
        return cls(model=m, feature_cols=feature_cols,
                   threshold=threshold, enabled=True)

    @classmethod
    def load_latest(cls, models_dir: str | Path, threshold: float = 0.55) -> "ScoreGate":
        """在 models_dir 下按文件名挑最新的 .ubj。"""
        d = Path(models_dir)
        ubjs = sorted(d.glob("xgb_*.ubj"))
        if not ubjs:
            logger.warning("未找到模型文件;降级为 passthrough")
            return cls.passthrough()
        return cls.load(ubjs[-1], threshold=threshold)

    def score(self, features: dict) -> tuple[float, bool]:
        """返回 (prob, accept)。"""
        if not self.enabled or self.model is None:
            return 1.0, True
        if self.feature_cols is None:
            raise RuntimeError("ScoreGate 无 feature_cols,无法评分")
        row = np.array(
            [[float(features.get(c, 0.0)) for c in self.feature_cols]],
            dtype=float,
        )
        # 避免 NaN
        row = np.nan_to_num(row, nan=0.0, posinf=0.0, neginf=0.0)
        prob = float(self.model.predict_proba(row)[0, 1])  # type: ignore[attr-defined]
        return prob, prob >= self.threshold


__all__ = ["ScoreGate"]
