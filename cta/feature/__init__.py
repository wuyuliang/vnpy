"""
CTA 特征工程模块

按类别组织：
    - trend:          趋势类特征（均线、MACD、ADX 等）
    - momentum:       动量类特征（RSI、ROC、CCI 等）
    - volatility:     波动率类特征（ATR、布林带、历史波动率等）
    - volume:         成交量与持仓量类特征（OBV、量比、持仓变化等）
    - pattern:        价格形态类特征（K线形态、支撑阻力等）
    - price_action:   Al Brooks 价格行为特征（信号K线、趋势结构、突破等）
    - cross_section:  截面类特征（品种间排名、z-score 等）
    - calendar_feat:  日历类特征（星期、月份、季节性等）

特征生成：
    python3 -m cta.feature.generate_features             # 天级+分钟级全部
    python3 -m cta.feature.generate_features --interval day  # 仅天级

输出目录：
    cta/feature/feature/day/{symbol}.parquet             # 单品种天级
    cta/feature/feature/day/_all_symbols.parquet          # 全品种合并(含截面)
    cta/feature/feature/minute/{symbol}.parquet           # 单品种分钟级
    cta/feature/feature/minute/_all_symbols.parquet       # 全品种合并(含截面)
"""
