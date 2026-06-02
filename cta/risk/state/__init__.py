"""State layer: persisted manifests and rolling trackers.

每个 state 组件独立持久化（fail-open）：
- ``ScoreQuantileManifest``：train 阶段产 JSON，sim/live/OOT 只读
- ``BucketPnlTracker``：on_trade 滚动累计 → JSON
- ``DdSignalProvider``：包装 EquityTracker，提供 max(cumulative, weekly) dd
"""
from cta.risk.state.bucket_pnl_tracker import (
    BucketKey,
    BucketPnlTracker,
    classify_score_bucket,
)
from cta.risk.state.consecutive_loss_tracker import ConsecutiveLossTracker, LossKey
from cta.risk.state.daily_var_tracker import DailyVaRTracker
from cta.risk.state.dd_signal_provider import DdSignalProvider
from cta.risk.state.execution_quality_tracker import (
    ExecutionQualityMetrics,
    ExecutionQualityTracker,
)
from cta.risk.state.intraday_profit_tracker import (
    IntradayProfitState,
    IntradayProfitTracker,
)
from cta.risk.state.liquidity_snapshot_tracker import (
    LiquiditySnapshot,
    LiquiditySnapshotTracker,
)
from cta.risk.state.rollover_calendar import RolloverCalendar, RolloverEntry
from cta.risk.state.score_distribution_tracker import (
    ScoreDistributionTracker,
    normalize_score_value,
)
from cta.risk.state.score_quantile_manifest import (
    ScoreQuantileEntry,
    ScoreQuantileManifest,
    build_from_predictions,
)

__all__ = [
    "BucketKey",
    "BucketPnlTracker",
    "ConsecutiveLossTracker",
    "DailyVaRTracker",
    "DdSignalProvider",
    "ExecutionQualityMetrics",
    "ExecutionQualityTracker",
    "IntradayProfitState",
    "IntradayProfitTracker",
    "LiquiditySnapshot",
    "LiquiditySnapshotTracker",
    "LossKey",
    "RolloverCalendar",
    "RolloverEntry",
    "ScoreDistributionTracker",
    "ScoreQuantileEntry",
    "ScoreQuantileManifest",
    "build_from_predictions",
    "classify_score_bucket",
    "normalize_score_value",
]
