# multi_timeframe_trend 最新代码评审

## 评审基线

- 分支：`feature`
- HEAD：`fe6b1ebf2066293cbca7df250863bdf37a261f3b`
- 范围：`multi_timeframe_trend` 的策略规则、回放引擎、风险控制、成交额选池、
  元数据准备、报告和当前未提交的图表展示改动。
- 工作区不是 clean。本评审包含 `charts.py`、`report.py`、`runner.py`、相关测试的
  未提交改动，以及未跟踪的 `cta/config/futures_display_names.py`。
- 回归验证：`python3 -m pytest -q cta/strategy/tests`，结果为
  `597 passed in 120.85s`。

本轮没有重复 2026-09-04 评审中已经修复的 R-01 至 R-10。结论按影响排序；
“高”表示会改变回测交易、选池或正式结果，“中”表示风险/审计口径错误或交付不完整，
“低”表示展示结果错误但不改变成交。

## 结论摘要

| # | 严重度 | 问题 | 影响条件 | 证据 |
|---|---|---|---|---|
| N-01 | 高 | 止盈触发后用当根新峰值重算的地板成交 | 默认开启 | 实测确认 |
| N-02 | 高 | 成交额表过期或重建失败后仍被静默使用/放行 | 开启成交额补池或闸门 | 代码确认 |
| N-03 | 中 | 无日期乘数被回填为自 `date.min` 起有效 | 日线成交额兜底 | 代码与测试确认 |
| N-04 | 中 | 部分减仓会改变后续止盈地板的 R 单位 | 隔夜或跨休市部分减仓 | 实测确认 |
| N-05 | 中 | 一手合约未实际减仓仍计入“已缩放入场” | 最小一手保护生效 | 代码确认 |
| N-06 | 中 | 报告先落盘、图表后生成，失败会留下不可重试的半成品 | 任一图表异常 | 代码确认 |
| N-07 | 中 | `trades.csv` 未版本化扩列，且依赖未跟踪模块 | 当前工作区改动 | 工作区确认 |
| N-08 | 低 | 图表数量和退出方向文案不准确，单图缺少事后区警告 | 图表报告 | 代码确认 |

## 处理决议（2026-09-05）

- **N-01 已修复**：地板击穿根只登记待退出，下一根 1 分钟 K 线按开盘对手价及既有不利滑点成交。
- **N-02 放行**：保留现有 fail-open 行为，不修改。
- **N-03 已修复**：无 `known_at/effective_from` 的乘数不再进入因果历史。
- **N-04 已修复**：价格与 R 的标尺固定使用建仓初始手数，允许回吐的 R 按剩余手数比例收紧。
- **N-05 已按最终规则修复**：缩放入场仍允许最少一手；后续减仓若计算保留量为零，则直接清仓。
- N-06 至 N-08 本次不修改。

## N-01 高：止盈成交读取了当根新地板

### 问题

`_manage_open_position` 先用旧 `profit_floor_price` 判断当根是否触发，随后立即调用
`_update_excursions` 和 `_refresh_profit_floor`。进入成交分支后，排序判断和
`_profit_floor_exit` 又从 `position.profit_floor_price` 读取数值，此时读到的已经是
包含当根最高价/最低价的新地板。

证据：

- `cta/strategy/multi_timeframe_trend_backtest/engine.py:429` 至 `:459`
- `cta/strategy/multi_timeframe_trend_backtest/engine_components/drawdown.py:46` 至 `:75`
- 设计文档明确要求地板“自下一分钟起生效”：
  `cta/strategy/docs/2026-09-04-multi-timeframe-trend-round3-position-sizing-design.md:603`

构造多头旧地板 103、当根 `open=105, high=110, low=102` 后，当前代码输出：

```text
old_floor=103.0
refreshed_floor=107.5
exit_reference=105.0
exit_price=104.9
```

按旧地板触发时，参考价应为 `min(105, 103)=103`，而不是 105。当前结果借用了
当根最高价，形成乐观成交并直接改变 `trades.csv`、权益曲线和后续回撤状态。

### 建议修复

用上一根确认的地板判断触发，触发根只写入 `PROFIT_FLOOR` 待退出状态。下一根
1 分钟 K 线按开盘对手价成交，不允许回填到触发根，也不再用地板价提供乐观成交。

### 必补测试

- 多头：旧地板被击穿且当根创新高，仍在下一根开盘成交。
- 空头：旧地板被击穿且当根创新低，仍在下一根开盘成交。
- 当根只触及新地板、不触及旧地板时，不得在当根退出。

## N-02 高：成交额覆盖失效时仍继续正式回测

### 问题

`_turnover_table_for` 发现表过期后尝试重建，但重建异常或重建结果被拒绝时直接返回旧表。
调用方只判断 `table.empty`，非空旧表会继续用最后 20 个历史日期选池，并把
`turnover_universe_asof` 记录成当前回测起点，掩盖实际覆盖终点。

另外，逐日成交额闸门直接重新读取磁盘文件。若重建成功但原子写入失败，补池使用内存中的
新表，逐日闸门却使用旧表或空表；空表和缺日期在闸门中均为 fail-open，正式状态不会因此
降级。

证据：

- 旧表继续返回：`cta/strategy/multi_timeframe_trend_backtest/runner.py:1618` 至 `:1681`
- 选池只检查是否为空：`cta/strategy/multi_timeframe_trend_backtest/runner.py:1554` 至 `:1589`
- 回放重新从磁盘加载：`cta/strategy/multi_timeframe_trend_backtest/runner.py:651` 至 `:659`
- 空表/缺日期放行：
  `cta/strategy/multi_timeframe_trend_backtest/engine_components/gates.py:56` 至 `:130`

默认配置关闭成交额闸门，因此默认回测不受影响；一旦用户显式启用
`--include-top-turnover` 或 `turnover_share_threshold`，该问题会改变品种池或让闸门
静默失效，却仍可能产出 `COMPLETE`。

### 建议修复

让加载/重建函数返回表、实际覆盖终点和来源状态。显式开启成交额功能时，覆盖不到要求日期
必须写入 `metadata_gaps` 并停止正式结果，不能退回旧表继续排名。成功重建的同一张内存表
应同时传给补池和回放；落盘失败可以告警，但不能在同一运行中切换数据版本。

### 必补测试

- 旧表非空但覆盖终点早于 `start`，且重建抛错时，不得继续补池。
- 重建结果被 `_rebuild_rejection` 拒绝时，报告状态不得为 `COMPLETE`。
- 新表生成成功但落盘失败时，补池与逐日闸门仍使用同一对象。
- 闸门开启且请求日期没有 cohort 时，不得静默放行正式结果。

## N-03 中：无日期乘数被当作自古有效

### 问题

扁平 `contract_specs.csv` 若只有 `root_symbol` 和 `contract_size`，代码把
`known_date`、`effective_date` 都设为 `date.min`。这会把当前/未知时点的乘数应用到全部历史
日线成交额，并可能改变历史成交额排名。审计结果只记录“已知乘数数量”，没有记录该假设，
也不会把报告降级为 `NON_CAUSAL_SCENARIO`。

证据：

- `cta/data_code/build_symbol_turnover.py:62` 至 `:113`
- 当前测试明确接受无日期文件：
  `cta/strategy/tests/test_symbol_turnover_universe.py:485` 至 `:491`

分钟数据自带供应商 turnover 时不需要乘数；风险集中在缺分钟数据、改用
`volume * close * contract_size` 的日线兜底品种。

### 建议修复

无 `known_at/effective_from` 的乘数不能进入因果成交额表。若业务仍需要 sim/live 默认值，
应把行标记为假设来源并沿报告链路降级为 `NON_CAUSAL_SCENARIO`，不能用 `date.min` 伪造
历史有效期。正式选池只接受有日期和来源的乘数。

### 必补测试

- 无日期乘数不能出现在正式历史成交额中。
- 假设模式必须输出根品种、字段、使用日期和 fallback，并降级报告状态。
- 有 `known_at/effective_from` 的乘数仍按交易日正确切换。

## N-04 中：部分减仓改变止盈地板的 R 单位

### 问题

`initial_risk_cash` 在入场时按初始手数冻结，但 `_position_point_value` 使用剩余
`position.quantity`。部分平仓后，同样的价格涨幅会被换算成更小的 `peak_r`，再把目标 R
转回价格时又要求更大的价格移动。

证据：

- 当前手数参与换算：
  `cta/strategy/multi_timeframe_trend_backtest/engine_components/drawdown.py:15` 至 `:43`
- 部分退出直接减少当前手数：
  `cta/strategy/multi_timeframe_trend_backtest/engine.py:2761` 至 `:2773`
- 最终 MFE 审计反而使用初始手数：
  `cta/strategy/multi_timeframe_trend_backtest/engine.py:2812` 至 `:2814`

构造初始 10 手、减至 5 手、每手价格已走出 2R 的仓位，当前 `_peak_unrealized_r`
返回 1R，地板为入场价；按初始风险单位则应为 2R，地板高出 1R。于是同一笔交易是否先
发生隔夜/跨休市减仓，会改变后续价格型止盈规则。

### 建议修复

价格/R 双向换算使用 `initial_quantity`，避免减仓重写历史峰值；允许回吐量按
`quantity / initial_quantity` 缩放。默认允许回吐 1R 时，减至半仓后只允许回吐 0.5R。

### 必补测试

- 地板尚未启用前先减半，2R 峰值应得到 1.5R 地板，而不是未减仓时的 1R 地板。
- 地板已启用后减半，地板不下降，后续允许回吐量按剩余手数比例缩放。
- 多头、空头对称。

## N-05 中：一手仓位的后续减仓被强制保留一手

### 问题

缩放入场允许把不足一手的计算结果恢复为一手，这是为了让小风险预算的合约仍可参与。
但跨休市高缺口风险减仓也用 `max(kept, 1)` 强制至少保留一手，因此一手仓位会生成
`PRE_BREAK_GAP_RISK_REDUCTION` 决策，却实际平仓零手。

证据：

- 最小一手入场：`cta/strategy/multi_timeframe_trend_backtest/engine.py:699` 至 `:736`
- 后续减仓强留一手：
  `cta/strategy/multi_timeframe_trend_backtest/engine_components/gates.py:225` 至 `:237`

这会让一手仓位绕过已触发的高缺口减仓保护，并留下原因码与实际成交不一致的审计记录。

### 建议修复

保留缩放入场阶段的最少一手规则；进入后续减仓阶段时直接使用向下取整后的保留手数，
若结果为零则清仓。

### 必补测试

- `base=1, factor=0.5` 的缩放入场仍成交一手。
- 一手持仓随后触发 `pre_break_gap_risk_scale=0.5` 时，平仓数量为一手。

## N-06 中：图表失败会留下不可重试的半成品目录

### 问题

runner 先调用 `publish_backtest_report` 创建最终目录并写完 CSV/JSON/Markdown，之后才调用
`render_opportunity_charts`。图表函数又以 `exist_ok=False` 创建子目录。字体、图片、上下文
或磁盘异常发生时，命令返回失败，但最终 run 目录已经存在；使用同一个显式 `run-id`
重试会在报告目录创建阶段再次失败。

证据：

- 报告先发布、图表后执行：
  `cta/strategy/multi_timeframe_trend_backtest/runner.py:748` 至 `:806`
- 最终目录立即创建：`cta/strategy/multi_timeframe_trend_backtest/report.py:424` 至 `:426`
- 图表目录也拒绝已存在：
  `cta/strategy/multi_timeframe_trend_backtest/charts.py:86` 至 `:88`

### 建议修复

把整次输出写入同级 staging 目录，CSV、JSON、Markdown 和图表全部成功后再原子重命名为
最终 run 目录。失败时只清理本次 staging，不触碰既有正式目录。

### 必补测试

- 注入图表异常后，最终 run 目录不存在。
- 同一 `run-id` 修复异常后可以直接重试成功。
- 已存在的完整 run 目录仍不可覆盖。

## N-07 中：`trades.csv` 契约漂移且当前依赖未纳入版本控制

### 问题

当前未提交改动在发布层给 `trades.csv` 第二列插入 `symbol_name`，但引擎仍声明
`TRADE_COLUMNS` 为规范列集合。测试由“发布 CSV 等于引擎契约”改成允许额外列，导致同名
产物出现两套 schema。此前依赖逐字节金标准的回归流程也会全部变化，却没有 schema
version 或迁移说明。

同时，`charts.py` 和 `report.py` 已导入 `cta.config.futures_display_names`，该模块在评审时
仍是 untracked。若只提交 tracked diff，干净 checkout 会在导入 runner 时直接失败。

证据：

- 插列：`cta/strategy/multi_timeframe_trend_backtest/report.py:84` 至 `:97`
- 引擎契约：`cta/strategy/multi_timeframe_trend_backtest/engine.py:133`
- 放宽测试：`cta/strategy/tests/test_multi_timeframe_trend_backtest.py:3215` 至 `:3225`
- `git status --short` 显示 `?? cta/config/futures_display_names.py`。

此外，名称查找固定读取默认排名表，没有接收 runner 的 `--ranking-csv`，所以自定义排名表
选出的品种与展示名称可能来自不同文件。

### 建议修复

优先保留 `trades.csv == TRADE_COLUMNS`，把中文名放到 `trades_display.csv` 或单独的
`symbol_labels.csv`。若确定中文名属于规范 schema，则统一修改 `TRADE_COLUMNS`、增加
schema version，并明确金标准迁移。名称数据源应由 runner 显式传入并在 context 中记录
路径/哈希。提交导入方时必须同时提交新模块。

### 必补测试

- 干净 checkout 导入 runner 成功。
- 自定义 `--ranking-csv` 同时驱动选池和显示名称。
- 规范 `trades.csv` 的列顺序由单一 schema 常量决定。

## N-08 低：图表审计文案不准确

### 问题

`_chart_guide` 无论 `traded/all/none` 模式，都写“生成 `len(index)` 张图”；实际上
`index` 始终包含全部候选，真正生成数量应统计非空 `chart_path`。当前预览还对所有方向
统一写“卖出”，空头退出应为买入平仓。单张 PNG 原有的
“SIGNAL 右侧仅供事后复盘”警告被删，只在旁边的 Markdown 指南保留，图片单独流转时会
失去因果边界说明。

证据：

- 数量文案：`cta/strategy/multi_timeframe_trend_backtest/charts.py:654` 至 `:663`
- 固定“卖出”：`cta/strategy/multi_timeframe_trend_backtest/charts.py:237` 至 `:242`
- 事件窗口确实包含信号后数据：
  `cta/strategy/multi_timeframe_trend_backtest/charts.py:580` 至 `:605`

### 建议修复

图数改为 `index["chart_path"].astype(bool).sum()`；退出文案按方向显示“卖出平仓/买入平仓”；
在 PNG 内恢复简短的事后区域警告，保留当前阴影作为辅助而不是唯一提示。

### 必补测试

- `none/traded/all` 三种模式的 Markdown 图数分别正确。
- 空头图标题不显示“卖出”退出。
- 单图仍包含事后区域警告。

## 已核查且未发现问题

- `allow_runtime_defaults=True` 产生的执行机制假设会通过 `assumed_mechanics` 传给报告，
  `build_official_summary` 将结果标为 `NON_CAUSAL_SCENARIO`，不会写入正式绩效。
- 日线与 5 分钟特征未发现 `shift(-n)`、centered rolling 或 backfill；日线对齐继续使用
  backward `merge_asof`。
- 入场 stop order 在信号时刻只创建计划，事件循环要到下一分钟事件才能撮合。
- 实际持仓换月时继续要求旧合约执行 bar，缺失会以
  `BLOCKED_ROLL_EXECUTION_BAR` fail closed。
- P-04 聚合缓存键包含算法版本、输入内容哈希、周期和完整时段模板；本轮未发现旧缓存跨
  算法复用。

## 建议执行顺序

1. N-01：先修同根分钟线穿越式成交，并落多空回归测试。
2. N-04：冻结部分减仓后的 R 口径；该项也会改变回测结果，应与纯报告修复分开。
3. N-02：统一成交额表覆盖状态和同一运行的数据对象，禁止显式功能静默失效。
4. N-03：移除 `date.min` 伪历史；若保留默认值，只能进入 `NON_CAUSAL_SCENARIO`。
5. N-05：修正缩放审计，区分策略状态与实际手数变化。
6. N-07：在提交当前展示改动前先确定并版本化 CSV 契约，纳入新模块。
7. N-06：把完整报告改成 staging 后原子发布。
8. N-08：最后修正文案与单图因果警告。

N-01、N-02、N-03、N-04 会改变交易或选池结果；N-05 至 N-08 应保持引擎交易结果不变。
后四项验收时至少比较 `artifacts.trades`，若保留规范 `trades.csv`，还应继续做逐字节比较。

## 验证缺口

本轮运行了完整策略单测，但没有重跑多品种长区间回测，也没有生成新的正式绩效结论。
现有 597 个测试全部通过不能覆盖 N-01，因为已有止盈集成样本在触发分钟没有继续创新峰值；
也不能覆盖 N-02，因为现有成交额测试明确把缺日期 cohort 作为 fail-open 行为。
