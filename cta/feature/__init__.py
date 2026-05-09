"""
CTA 特征工程模块

按类别组织：
    - trend:                  趋势类（均线、MACD、ADX 等）
    - momentum:               动量类（RSI、ROC、CCI 等）
    - volatility:             波动率类（ATR、布林带、历史波动率等）
    - volume:                 成交量与持仓量类（OBV、量比、持仓变化等）
    - pattern:                价格形态类（K线形态、支撑阻力等）
    - price_action:           Al Brooks 价格行为（信号 K 线、趋势结构、突破等）
    - price_action_advanced:  Al Brooks 高级特征
    - price_action_context:   Al Brooks 上下文特征
    - price_action_supplement:Al Brooks 形态补充
    - cross_section:          截面类（品种间排名、z-score 等）
    - calendar_feat:          日历类（星期、月份、季节性等）
    - minute_tod:             分钟级同比特征
    - stats_feat:             统计 / 分形类特征
    - volatility_regime:      波动率体制
    - multi_timeframe:        多周期对齐
    - regime:                 市场状态机
    - composite:              综合评分
    - entry_stop:             入场 / 止损建议价

特征生成入口：
    python3 -m cta.feature.run_all_features                    # 全频率全品种
    python3 -m cta.feature.run_all_features --interval day     # 仅天级
    python3 -m cta.feature.run_all_features --symbols RB0      # 指定品种

输出布局：
    cta/data/feature/{interval}/{SYMBOL}/{YYYY-MM-DD}.parquet  # 按日分片
    cta/data/feature/{interval}/_all_symbols.parquet           # 截面合并

详细字段见 cta/feature/FEATURES.md。
"""
