"""模型层: MFE/MAE 标注 + XGBoost 训练 + 评分门控。"""

from cta.strategy.brooks.core.model.labeler import label_setup
from cta.strategy.brooks.core.model.score_gate import ScoreGate

__all__ = ["label_setup", "ScoreGate"]
