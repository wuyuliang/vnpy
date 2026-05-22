# cta/skills/configs/

## 主要做什么

**研究 / 验收**用的 YAML 配置，和 [cta/skills/overview/](../overview/) 配套使用。**不是**生产 config——线上 config 在 [cta/config/](../../config/)。

## 关键文件

| 文件 | 作用 |
|---|---|
| [research_pool.yaml](research_pool.yaml) | 研究池子的品种筛选规则：流动性 / 历史长度 / 波动率门槛；被 [overview/research_pool.py](../overview/research_pool.py) 读取 |
| [acceptance.yaml](acceptance.yaml) | 实验验收阈值：`min_win_rate` / `min_sharpe` / `max_drawdown` 等硬下限；被 [overview/acceptance.py](../overview/acceptance.py) 读取 |

## 详细过程

```
[研究 / 实验完成]
    │
    ▼
acceptance.evaluate(oot_summary, cfg=load("acceptance.yaml"))
    └─ 判定 accepted / needs_iteration / rejected
```

## 注意事项

- **YAML 阈值改动要写动机**：把"为什么松/严"的一句话写到 PR 描述 + [cta/report/change_log.md](../../report/change_log.md)。
- **不能 import 这里的 YAML 到生产代码**：实盘 / OOT 的硬阈值在 [cta/config/](../../config/) 的 Python dataclass 里。
- **格式约定**：字段名一律 `snake_case`；阈值单位（pct / abs / count）必须写在 key 后缀里（如 `min_win_rate_pct: 50`）。
