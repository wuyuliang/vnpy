# cta/skills/overview/

## 主要做什么

**项目方法论的代码化**：把"研究池子怎么选"、"回测原则"、"实盘原则"、"迭代节奏"、"验收口径"这些团队约定写成可调用模块，让 skill / 策略 / pipeline 用同一份"判断标准"，避免每个人按记忆做。

对应的纯文档版本在 [cta/skills/cta_skills/00_overview_methodology/README.md](../cta_skills/00_overview_methodology/README.md)。

## 关键文件

| 文件 | 主题 |
|---|---|
| [research_pool.py](research_pool.py) | 研究池子的品种选择规则（流动性 / 历史长度 / 波动率门槛），输入接 [cta/skills/configs/research_pool.yaml](../configs/research_pool.yaml) |
| [backtest_principles.py](backtest_principles.py) | 回测必须做的检查（手续费滑点 / 跳空 / 时段 / 出错 fallback），用 assertion 形式落地原则 |
| [live_principles.py](live_principles.py) | 实盘必须满足的前置条件（仿真天数、kill_switch 配置、parity diff 阈值） |
| [iteration.py](iteration.py) | 策略迭代节奏：什么时候 promote 影子单到实盘、什么时候回滚 |
| [acceptance.py](acceptance.py) | 实验验收阈值：win_rate / sharpe / max_drawdown 的硬下限，配 [cta/skills/configs/acceptance.yaml](../configs/acceptance.yaml) |

## 详细过程

```
[新策略验收] cta/model/model_pipeline.py 跑完
    │
    ▼
acceptance.evaluate(oot_summary)
    ├─ win_rate ≥ cfg.min_win_rate ?
    ├─ sharpe ≥ cfg.min_sharpe ?
    ├─ max_drawdown ≤ cfg.max_drawdown ?
    └─ → "accepted" / "needs_iteration" / "rejected"
    │
    ▼
iteration.next_step(verdict, history) → 下一步动作建议
    │
    ▼
[切实盘前] live_principles.preflight(strategy_id)
    ├─ sim_days >= cfg.min_sim_days ?
    ├─ parity_diff_pct < cfg.max_parity_diff ?
    └─ kill_switch 配置完整 ?
```

## 注意事项

- **本目录定义"什么算合格"**：任何"硬阈值"决定都应在此或其 yaml；策略侧/pipeline 侧不要散布阈值。
- **改阈值要写明动机**：阈值变更必须在 `cta/report/change_log.md` 留 1-2 行说明（为什么松/严），便于回溯。
- **不能反向控制业务**：本目录只**判断/建议**，不主动改 config 或发单。
- **与 cta_skills 文档同步**：如果 [cta_skills/00_overview_methodology/](../cta_skills/00_overview_methodology/) 的文档与本目录代码出现不一致，以代码为准并立即修文档。
- **测试**：`pytest cta/skills/overview/tests/ -v`，每条原则至少一个 pass / fail 用例。
