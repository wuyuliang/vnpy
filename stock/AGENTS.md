# 项目目标
本目录用于基于 vn.py 风格实现 A 股研究代码，但当前阶段只覆盖：

1. 下载沪深股票日线数据
2. 生成四类股票交易机会，包括 `ma5_ma10_big_bull`
3. 输出 analysis K 线图和大牛股退出研究图

---

# 硬性边界
你只能修改以下路径：

- `stock/**`

禁止事项：

- 禁止修改 `stock/` 外的任何文件
- 禁止修改 vn.py 主框架、网关、数据库底层
- 禁止删除或覆盖用户已有的非 `stock/` 文件

---

# 当前范围
支持两类股票池：

- 样本股票池：沪市 10 只 + 深市 10 只
- 全市场股票池：Tushare `stock_basic` 返回的当前上市沪深 A 股

股票过滤：

- 默认排除名称以 `ST`、`*ST`、`S*ST` 开头的股票
- 股票名称缺失时按不可分析处理，不允许离线 fallback 绕过 ST 过滤
- 过滤同时作用于股票池、机会表、analysis 图和每日 report

默认只做日线，不做：

- 仓位管理
- 机会筛选后的交易执行
- 组合级资金曲线回测
- 实盘接入
- 分钟级下载

---

# 目录职责
- `stock/config/`
  - 股票池、默认参数、路径约定
- `stock/data_code/`
  - 数据下载与字段标准化
- `stock/strategy/`
  - 信号评估与机会扫描
- `stock/analysis/`
  - K 线图生成与结果索引
- `stock/backtest/`
  - 研究型退出规则模拟，不包含真实交易和仓位管理
- `stock/run/`
  - 一键串联下载、扫描、出图
- `stock/tests/`
  - 单元测试与最小回归测试
- `stock/data/`
  - 下载后的股票数据
- `stock/report/`
  - 机会扫描结果

---

# 数据规则
统一字段尽量保持：

- `symbol`
- `exchange`
- `interval`
- `datetime`
- `open`
- `high`
- `low`
- `close`
- `pct_chg`
- `volume`
- `open_interest`
- `turnover`

默认输出路径：

- 日线数据：`stock/data/origin/day/{symbol}.csv`
- 机会结果：`stock/report/opportunities/{run_id}_stock_signal_opportunities.csv`
- 每日机会图目录：`stock/report/opportunity_date/{run_id}/{rank}_{YYYY-MM-DD}/*.png`
- 图表结果：`stock/analysis/{run_id}/`

每日增量运行：

- `python3 -m stock.run.sample_pipeline --universe all --latest-days 5`
- `--latest-days N` 只下载最近 N 个开市交易日，并合并到已有日线 CSV，不覆盖历史 K 线
- `--latest-days N` 只输出最近交易日区间内的机会和图表

---

# 开发规则
- Python 3.10+
- 必须加类型注解
- 关键函数必须有 docstring
- 路径不要硬编码到仓库外
- 关键步骤要有日志
- 优先小函数、少副作用
- 每个脚本都要支持命令行入口

---

# 策略开发规范
当前实现四个信号：

- `bull_pullback_continuation`
- `breakout_pullback_continuation`
- `volume_spike_up`
- `ma5_ma10_big_bull`

信号目标：

- `bull_pullback_continuation`：在 `ema5 >= ema10 >= ema20` 背景下，捕捉价格回踩 EMA20 附近后的顺势多头机会
- `bull_pullback_continuation`：量能必须满足最近一天成交量翻倍，或近 3 日均量 >= 1.5 倍近 10 日均量
- `breakout_pullback_continuation`：先突破前高，再回踩突破位附近，随后重新站回突破结构
- `breakout_pullback_continuation`：信号日收盘价必须低于过去约一年半到当天最高价的一半
- `volume_spike_up`：当天上涨，且当天成交量 >= 前 5 个交易日平均成交量的 2 倍
- `volume_spike_up`：信号日收盘价必须低于过去约一年半到当天最高价的一半
- `ma5_ma10_big_bull`：以 `MA5` 从下向上穿越 `MA10` 为触发，再用趋势、相对强度、量能、涨停/活跃基因、不过热约束、每日 TopN 和 symbol 冷却筛选候选
- `ma5_ma10_big_bull`：`rs_60d_pct` 必须按机会日全部非 ST 股票计算，不允许只在候选子集内排名
- `score_and_filter_big_bull_opportunities()` 有大牛股行时必须显式传入 `market_returns_by_date`，禁止隐式回退候选子集排名
- `ma5_ma10_big_bull`：涨停基因按板块阈值计算，主板默认 9.8%，创业板/科创板默认 19.8%
- `ma5_ma10_big_bull`：默认 `big_bull_score >= 70`、同日最多 30 只、同一 symbol 按机会日 20 个自然日内最多一次；先剔除冷却 symbol，再取每日 TopN

主机会表、普通 analysis 和每日报告只输出买点，不输出卖点、真实仓位或交易单。
`stock/backtest/big_bull_exit_research.py` 仅在独立研究产物中模拟止损和趋势退出，并按 symbol 将买卖点画在同一张图中。
每日机会使用上一交易日完整日 K 判断，`opportunity_date` 记为下一交易日。
每日目录仍只列出该 `opportunity_date` 当天入选的 symbol，但目录内 PNG 是研究回看汇总图：统一画到本次运行 `end`，并展示该 symbol 在本次运行区间内的全部买点。
同一 symbol 的全部 signal_type 必须合并到一张 PNG；买点使用 signal_type 颜色的竖向虚线加向上箭头。
价格和成交量图每两个月绘制一条时间辅助竖线，价格图下方标注 `YYYY-MM`。
日报回看汇总图包含当日之后的信息，不能作为 point-in-time 历史决策快照；机会筛选和 CSV 本身仍必须保持无未来函数。
每日目录使用 `0001_YYYY-MM-DD` 形式按倒序编号，确保最近日期在文件管理器中排在最上面。
`volume_spike_up` 专用每日目录输出到 `stock/report/opportunities_date_spike_up/{run_id}/`。
`breakout_pullback_continuation` 专用每日目录输出到 `stock/report/opportunities_date_break_out/{run_id}/`。
`ma5_ma10_big_bull` 专用每日目录输出到 `stock/report/opportunities_date_ma5_ma10_big/{run_id}/`。
大牛股单信号运行时，专用日报复用普通日报 PNG 的硬链接或文件复制，不重复渲染。
退出研究只允许在精确的 `opportunity_date` 日 K 开盘入场；缺少该日 K 线时必须标记不可交易，初始止损从入场日开始检查。
不可交易的退出研究行可以保留在结果表中，但图表不得绘制虚假买卖点，也不得因 `pd.NA` 中断整批出图。
近两年涨跌停统计窗口必须同时限制起始 cutoff 和回测 `end`，不能读取 `end` 后的本地数据。

---

# 执行顺序
每次任务按以下顺序执行：

1. 先阅读本文件
2. 再阅读 `stock/stock.md`
3. 先补测试
4. 再实现代码
5. 运行最小命令验证
6. 输出修改文件、运行命令、结果位置、风险说明

---

# 默认原则
- 保守修改
- 只做当前要求的最小链路
- 先可运行，再扩展
- 先样本股，再全市场
