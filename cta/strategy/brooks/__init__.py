"""Brooks v3: 基于已有 pa_* 特征的多周期共振 + 风控 + XGBoost 门控。

模块职责:
- config/        参数与品种配置(ranking-driven),运行期 yaml
- core/          离线/在线共享的策略核心
  - features/    FeatureAdapter(统一 offline/online 取特征)
  - signal/      HTF bias -> MTF setup -> LTF entry
  - risk/        ATR sizing + 止损 + 组合回撤降仓
  - model/       MFE/MAE 标注 + XGBoost 训练 + 评分门控
  - trade_log.py TradeRecord 成交日志
  - strategy.py  BrooksV3Core 组合器
- backtest/      封装 vnpy BacktestingEngine 的批量入口
- online/        实时流式驱动(vnpy CtaTemplate 桥接)
- models/        XGBoost 模型产物(.ubj + metadata.json)
- report/        回测输出

详见 brooks_v3.md。
"""
