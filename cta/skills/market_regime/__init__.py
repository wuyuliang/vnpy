"""01 市场结构识别 / Market Regime Detection.

对应 cta/cta_skills/01_market_regime/ 下的 5 个 md。

设计要点
--------
- 所有子模块接 OHLCV DataFrame（列 `open/high/low/close/volume` + 可选
  `datetime`/`turnover`），返回**和输入等长**的新 DataFrame，新列以子模块
  前缀（trend_/range_/threshold_/vol_/align_）区分。
- 指标自己算，不依赖 cta/feature/price_action.py 的落盘特征；调用方在有
  pa_* 特征时可以把我们的列替换掉。
- 对 day / minute60 / minute30 / minute15 / minute5 / minute 六周期一视同仁，
  仅通过参数（如 ATR/ADX 窗口）适配。

子模块
------
- volatility          : §04 四态 (compression/expansion/low/high) + vol_target
- trend               : §01 趋势打分（score/dir/strength/maturity）
- range               : §02 震荡识别（score/upper/lower/width/age）
- breakout_threshold  : §03 临界打分 + 触发价
- alignment           : §05 HTF/MTF/LTF 三周期对齐
"""
from __future__ import annotations

# 子模块按需导入：避免尚未落地的模块引发 ImportError，保持
# `from cta.skills.market_regime.<submodule> import ...` 可单独加载。
# 完整实现后，下面的 try/except 全部会变成直通 import。
from cta.skills.market_regime.volatility import (  # noqa: F401
    VolState,
    compute_vol_state,
    vol_target_size,
)

try:
    from cta.skills.market_regime.trend import (  # noqa: F401
        TrendState,
        classify_maturity,
        compute_trend_state,
    )
except ImportError:  # pragma: no cover
    pass

try:
    from cta.skills.market_regime.range import (  # noqa: F401
        RangeState,
        compute_range_state,
        detect_range_age,
    )
except ImportError:  # pragma: no cover
    pass

try:
    from cta.skills.market_regime.breakout_threshold import (  # noqa: F401
        ThresholdState,
        compute_threshold_state,
        estimate_breakout_probability,
    )
except ImportError:  # pragma: no cover
    pass

try:
    from cta.skills.market_regime.alignment import (  # noqa: F401
        AlignmentResult,
        align,
        resolve_enter_price,
    )
except ImportError:  # pragma: no cover
    pass
