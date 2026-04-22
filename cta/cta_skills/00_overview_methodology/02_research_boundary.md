# 研究边界 / Research Boundary

> 归属章节：`00_overview_methodology/` · 前置技能：`01_objectives.md` · 关联：`cta/config/futures_meta.py`

## 1. Skill 定义

明确**我们研究什么、不研究什么**：品种集合、周期集合、策略族集合、成本假设集合。边界越清晰，越能聚焦，也越容易迭代。

## 2. 解决什么问题

- 痛点 1：品种太多无主次，每个品种都浅尝辄止。
- 痛点 2：什么都想做（趋势 / 套利 / 期权 / 股票），资源分散。
- 痛点 3：边界不写下来，半年后忘了"为什么当初不做 IC"。
- 增量价值：把"研究空间"固化成一份清单，任何新想法先在清单上看位置。

## 3. 适用市场 / 适用场景

- **品种类别**：初期聚焦「黑色 / 有色 / 能化」三大类中的主力品种，排除流动性差的小品种
- **周期**：`day` + `minute60 / minute30 / minute15 / minute5`，暂不做 `minute` 级别的纯高频
- **行情状态前提**：不做事件驱动、不做跨品种套利（留到 v3+）
- **不适用场景**：本 skill 只画边界，不产出信号

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| 日均成交额 ADV | `sum(volume * close)`/ N 日 | N=20 | ≥ 30 亿元为 A 档 | `cta/feature/symbols_research_ranking.csv` |
| 流动性分位 LP | 在全市场品种里的百分位 | — | Top 20% 才入池 | `cta/feature/symbols_research_ranking.csv` |
| 品种覆盖 Coverage | 有 feature 落盘的品种数 / 总研究池 | — | ≥ 80% 才算边界就绪 | `cta/feature/run_all_features.py` 产出 |

## 5. 常见策略映射

边界本身不是策略，但**直接输入到策略选择**：

### 研究池 v1（示例）
- A 档（初期）：`RB0.SHFE`、`HC0.SHFE`、`I0.DCE`、`CU0.SHFE`、`AL0.SHFE`、`MA0.CZCE`、`TA0.CZCE`、`PP0.DCE`
- B 档（扩展）：`FG0.CZCE`、`SA0.CZCE`、`EG0.DCE`、`V0.DCE`
- 排除：次主力（如某些品种的近月非主力）、低流动性（ADV < 5 亿）

### 周期配比
- day：趋势底仓（长期持有，周级别）
- minute60 / minute30：中频趋势 / 突破（日内持仓）
- minute15 / minute5：setup 级别（打分 / 入场）
- minute：暂不做

## 6. 代码模块设计

不新增 py 代码，但**约束**数据与特征覆盖脚本优先跑 A 档：

```text
cta/feature/
├── symbols_research_ranking.csv   # 全市场品种排序（已有）
└── run_all_features.py            # 批量特征（按 ranking 顺序）
```

```python
# 在 cta/config/ 下可加一个 research_pool.yaml（若后续有需求）
research_pool:
  A:
    - RB0.SHFE
    - HC0.SHFE
    - I0.DCE
    - CU0.SHFE
    - AL0.SHFE
    - MA0.CZCE
    - TA0.CZCE
    - PP0.DCE
  B:
    - FG0.CZCE
    - SA0.CZCE
    - EG0.DCE
    - V0.DCE
  intervals: [day, minute60, minute30, minute15, minute5]
```

```python
def in_research_pool(symbol: str, tier: str = "A") -> bool:
    """符号是否在指定 tier 的研究池中。"""
    ...

def resolve_research_symbols(tier: str = "A") -> list[str]:
    """按 tier 返回排序后的品种列表（可被 Brooks `resolve_symbols` 复用）。"""
    ...
```

## 7. 回测评估重点

- **必看指标（7）**：研究池总 PnL、池内品种胜率、池内品种平均 Sharpe、池覆盖率、跨品种相关性、单品种权重上限是否被触发、换手是否在容量内
- **分层评估**：A 档 vs B 档、按品种大类（黑色/有色/能化）
- **稳健性检验**：
  1. 从 A 档中随机删掉 1 个品种，整体 Sharpe 不应下降超过 15%
  2. 把 B 档加入池子后，总 Sharpe 不应下降（如下降说明 B 档噪声大）
  3. 研究池排序（ranking）更新后重跑，结果不应大幅变动

## 8. 常见错误

- 研究池过宽：把所有 70+ 品种一起回测，信号淹没在噪声里
- 研究池过窄：只做 RB，一叶障目
- 不维护 ranking：行情结构变化（如某品种上市 → 主力）却还在老池子里
- 忘记剔除：保证金上调、新合约上市会影响可交易性

## 9. 迭代方向

- v1：本文档定义研究池
- v2：自动从 ranking CSV 重新生成研究池，每月更新
- v3：加入跨品种相关性过滤，避免"多品种实际同一行情"
- v4：加入期权 / 股指 / 跨期套利扩展边界

## 10. 与其他 Skills 的关系

- **依赖**：`00_overview_methodology/01_objectives.md`（目标决定边界宽窄）
- **被依赖**：`08_data_and_backtest_infra/01_continuous_contract.md`（只对池内品种生成连续合约）、`09_ml_augmentation/04_feature_store.md`（只对池内品种维护特征）
- **互补**：`07_position_and_portfolio/03_sector_exposure_control.md`（大类配比需要边界做前提）
