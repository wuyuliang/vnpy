# Cycle V1 独立 CLI 设计

## 目标

为隔离包 `cta.strategy.brooks.cycle_v1` 增加独立 CLI。CLI 自动发现本地分钟数据中的全部根品种，每个交易日只允许前一完整交易日满足日线 `EMA(1) > EMA(3) > EMA(5)` 的品种进入 cycle_v1 多周期扫描。长、中、短周期由命令行配置，单位只接受分钟或小时。

入口：

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.runner \
  --symbols RB CU \
  --start 2026-01-01 \
  --end 2026-07-27 \
  --long-tf 1hour \
  --medium-tf 15min \
  --short-tf 5min \
  --initial-equity 200000
```

## 冻结语义

- `EMA(1)` 是日线收盘价；`EMA(3)`、`EMA(5)` 使用 `adjust=False`。
- 交易日 `D` 的准入值只来自 `D-1` 及以前的完整交易日日线，禁止使用 `D` 当天数据筛选 `D`。
- EMA 按实际合约 epoch 计算。主力合约切换后不跨跳空拼接 EMA，新合约至少积累 5 根完整日 K 后才能通过。
- 周期与 setup 信号使用 point-in-time 加法调整连续序列。每次可观察的主力切换只使用 session open 已知的新旧合约 `pre_settlement` 生成后续 adjustment，历史前缀永不重写；候选几何在风险计算前映射回当时实际合约，订单和 PnL 不使用连续价。
- “全部品种”指 `--data-root` 中可发现、可映射且未重复的全部根品种，不把目录别名重复计数。
- 周期只接受 `Nm`、`Nmin`、`Nh`、`Nhour`，并要求 `long > medium > short >= 1min`。
- 所有周期 K 线都从真实 1 分钟数据聚合，只使用完整 bucket，绝不跨交易时段断点拼 K 线。
- 相邻 parquet 分区若包含同一 `(contract_code, bar_end)`，只有除来源定位列外全部字段完全一致时才合并，并在 summary 记录 `partition_overlap_rows_removed`；任一字段冲突都以 `CONFLICTING_DUPLICATE_BAR` 阻断。
- 周期特征先在本地可用 warmup 前缀上计算，再按请求区间和 EMA 准入过滤，避免因筛选删除历史而改变指标。

## 数据与输出

分钟增量下载器接受任意 `end >= start` 的日期区间，并严格只下载命令行 `start..end` 内的数据。已有 parquet 默认跳过且不覆盖；策略预热只读取本地可用历史，特征与元数据继续使用既有可用性和 fail-closed 规则：

```bash
python3 -m cta.strategy.brooks.cycle_v1.backtest.market_data_update \
  --start 2026-01-01 \
  --end 2026-07-27 \
  --data-root cta/data/origin/minute \
  --audit-output cta/strategy/brooks/report/cycle_v1/20260823_data_update_20260101_20260727.json
```

CLI 通过 `cycle_v1/legacy_adapters` 只读复用 scalp 的分钟标准化、交易日历、历史费用和会话审计。被 scalp 标记为合约在同一会话内切换的 roll session 必须排除；截止自然日晚间属于下一交易日的尾部数据在会话分配前丢弃。每次运行生成不可覆盖的报告目录：

- `universe_daily.csv`：每日每品种前一日 EMA 值、准入结果及阻断原因。
- `cycle_snapshots.csv`：准入品种的长、中、短周期最新已完成市场周期状态。
- `metadata_coverage.csv`：每个实际合约交易日的 instrument、daily、lifecycle、status、cost、STOP capability 覆盖结果。
- `metadata_gaps.csv`：数据、历史规则或执行能力缺口。
- `candidates.csv`、`plans.csv`、`orders.csv`、`fills.csv`：完整机会与执行漏斗；候选记录实际合约 entry/stop/target/trade_mode，orders 按状态留痕，summary 按唯一订单统计，fills 漏斗只统计入场成交。
- `trades.csv`：实际合约往返交易、双边费用、滑点、净 PnL、MFE/MAE 和持有 bar 数。
- `daily_equity.csv`、`performance_by_group.csv`：包含零交易日在内的日权益与品种/板块/setup/cycle/方向分组结果。
- `rejections.csv`：每个决策时点的规则拒绝原因；候选级拒绝额外记录 candidate_id、risk_budget、loss_per_lot 和周期/几何详情。
- `summary.json`、`report.md`：参数、发现品种、漏斗、正式状态、拒绝汇总和候选级拒绝详情。

SHFE canonical metadata 已扩展至 `2026-07-27`。`execution_metadata.py` 从有来源和 `known_at` 的表构建 lifecycle、trading status、保守 bar matcher STOP capability、逐日保证金/涨跌停/压力费用快照，不使用 fallback。任一请求字段缺失时仍为 `BLOCKED_METADATA` 且正式绩效为 `null`；覆盖完整后使用共享 core、下一分钟激活、保守 OHLC 歧义、OCO 和真实合约账本生成净绩效。合约切换只能在新合约首根 bar 已知后生效，先取消旧合约挂单；若仍持有旧合约且 active-contract 数据不能提供旧合约执行 bar，则以 `BLOCKED_ROLL_EXECUTION_BAR` 阻断，禁止预读下一根 bar 后在上一根 bar 回填换月成交。零候选或零成交是合法的完整回放结果，必须报告 0 收益而不是伪造交易。

## 2026-08-23 实际结果

正式复核目录：

```text
cta/strategy/brooks/report/cycle_v1/
20260823_official_v6_20260101_20260727_1h_30m_5m/
```

本次 RB/CU 运行状态为 `COMPLETE`：`1626/1626` 个字段覆盖通过，135 个请求交易日进入日权益分母，72 个 EMA 合格品种日产生 5712 个周期快照。修复按实际合约 epoch 重置约 504 根 bar 周期历史的问题后，冻结规则产生 8 个 CU candidate。6 个因 1h 大周期为 `TRANSITION` 被 `LARGE_CYCLE_UNAVAILABLE` 拒绝；另外 2 个通过周期许可，但 20 万账户的窄通道预算为 400 元，压力成本后的单手损失分别为 5450.7 元和 2554.1 元，被 `ONE_LOT_EXCEEDS_RISK_BUDGET` 拒绝。因此仍为 0 order、0 round trip 和 0 收益。这是风险规则的真实结果，不得通过缩窄结构 stop 或伪造成交消除。

## 验收标准

- 修改未来日 K 不改变此前任何交易日的准入结果。
- 修改未来分钟 K 不改变此前周期快照。
- 合约切换日不会继承旧合约 EMA warmup。
- 非法周期、周期逆序、重复品种和未知品种明确失败。
- `--symbols all` 对 `CU0.SHF`、`CU_small` 等同根目录去重。
- 旧 cycle_v1、scalp 测试继续通过，CLI 有独立单测和真实数据冒烟验证。
- 完整覆盖时 `report.md` 必须打印具体官方绩效；零交易未定义指标使用 JSON `null`，CSV 空表仍保留稳定表头。
