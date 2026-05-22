# cta/skills/

## 主要做什么

**模块化技能库**：把 CTA 研究里反复用到的"能力组件"（regime 识别、价格行为、突破策略、过滤打分、仓位/组合管理、ML 增强、回测基础设施、实盘运维）拆成独立子包，配合 [cta_skills/](cta_skills/) 下的方法论 README，形成"技能树"。

策略文件 `cta/strategy/*.py` 应当**只是组装这些 skill**，而不是自己重写指标 / 信号 / 风控。

## 子目录结构

### 代码模块（可 import）
| 子目录 | 主题 | 关键文件 |
|---|---|---|
| [data_backtest/](data_backtest/) | 回测基础设施 | `event_driven_backtest.py` / `continuous_contract.py` / `rollover_rules.py` / `transaction_cost.py` / `trade_evaluation.py` |
| [filtering_scoring/](filtering_scoring/) | 候选过滤与评分 | `breakout_quality.py` / `context_score.py` / `risk_reward_score.py` / `setup_quality.py` / `ml_opportunity_model.py` |
| [live_ops/](live_ops/) | 实盘运维工具 | （内部模块） |
| [market_regime/](market_regime/) | 市场状态判别 | trend / range / regime switch detector |
| [ml_augmentation/](ml_augmentation/) | ML 辅助 | 与三段模型互补的轻量 ML 工具 |
| [overview/](overview/) | 总体方法论代码化 | |
| [position_portfolio/](position_portfolio/) | 仓位/组合规则 | （和 `cta/portfolio_logic/` 互补——`portfolio_logic` 是运行时状态机，这里是离线工具/规则） |
| [price_action/](price_action/) | 价格行为识别工具 | |
| [range_strategies/](range_strategies/) | 区间策略 | |
| [regime_switch/](regime_switch/) | regime 切换策略 | |
| [trend_strategies/](trend_strategies/) | 趋势策略 | |

### 配置与文档
| 子目录 | 内容 |
|---|---|
| [configs/](configs/) | YAML 配置：`acceptance.yaml`、`research_pool.yaml`（哪些品种入研究池/验收阈值） |
| [cta_skills/](cta_skills/) | **方法论文档树**：11 个章节 README，从 00_overview 到 10_live_ops；改 skill 代码前应先读对应章节 |
| [output/](output/) | skills 的离线产出（按需保留） |

## 详细过程（怎么"组装"出一个新策略）

```
[新策略想法]
   │
   ▼
[1] 读 cta_skills/00_overview_methodology/README.md 确认方法论定位
   │
   ▼
[2] 选择 skill 组件：
     ├─ market_regime/  ← 何时启用？
     ├─ price_action/   ← 用什么形态？
     ├─ filtering_scoring/  ← 怎么打分 / 过滤？
     ├─ position_portfolio/  ← 仓位规则？
     └─ data_backtest/  ← 回测怎么跑？
   │
   ▼
[3] 在 cta/strategy/<new_strategy>.py 里
     - import 上述 skill 组件
     - 写组装与参数（不要在 strategy 里重复写指标/规则）
     - 留一个 if __name__ == "__main__": main() 入口
   │
   ▼
[4] 用 cta/skills/data_backtest/event_driven_backtest.py 回测
   │
   ▼
[5] 结果落到 cta/report/backtest/<日期>_<策略>_<品种>_<周期>/
```

## 注意事项

- **代码模块 vs 方法论文档**：[cta_skills/](cta_skills/) 是**只读方法论文档**（已存在 README.md，禁止把它当代码目录用）；同名但去掉 `cta_skills/` 前缀的子目录（如 `cta/skills/market_regime/`）才是**可 import 的代码模块**。
- **`cta_skills/*/README.md` 不要重写**：它们记录了团队对"什么是好策略"的方法论共识，改动需 review。
- **skill 接口稳定**：被 `cta/strategy/*` 大量依赖的函数（如 `breakout_quality.evaluate(...)`）的签名修改必须在 PR 描述里列出所有 caller。
- **测试**：每个 skill 模块都有 `tests/`，跑 `pytest cta/skills/<module>/tests/ -v`。
- **`configs/`**：YAML 是研究/验收配置，**不是**线上 config（线上 config 在 [cta/config/](../config/)）。两者不要互相 import。
- **`cta/portfolio_logic/` 不放在这里**：组合执行运行时单独成包（`cta/portfolio_logic/`），因为它被 OOT 和实盘共享，重要性更高。
