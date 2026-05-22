# cta/strategy/brooks/config/

## 主要做什么

Brooks v3 的运行时参数与品种集配置。所有阈值 / 周期 / 风控参数都在这里集中维护，禁止散布到 [core/](../core/) 代码里。

## 关键文件

| 文件 | 作用 |
|---|---|
| [strategy.yaml](strategy.yaml) | 全量参数（周期、阈值、风控、模型超参、训练数据 range） |
| [params.py](params.py) | 把 yaml 加载到 `BrooksV3Params` dataclass，提供类型检查 + 默认值 |
| [symbols.py](symbols.py) | 按 ranking CSV 解析 top_n 品种集 |

## 详细过程

```
[加载]
    from cta.strategy.brooks.config.params import BrooksV3Params
    params = BrooksV3Params.from_yaml("cta/strategy/brooks/config/strategy.yaml")
        └─ 自动类型检查；缺字段抛错（默认值在 dataclass 里）

[品种集]
    from cta.strategy.brooks.config.symbols import load_top_n
    symbols = load_top_n("cta/feature/symbols_research_ranking.csv", n=10)
```

## 注意事项

- **`strategy.yaml` 是 source of truth**：[params.py](params.py) 的 dataclass 字段必须与 yaml key 名一一对应。改 yaml 加字段必须先改 dataclass，否则 from_yaml 会拒绝。
- **per_trade_pct = 0.001 默认**：和 v3 任务书一致；**不要**改这个默认值（实验时在 yaml 里覆盖）。
- **品种 ranking 路径**：默认 `cta/feature/symbols_research_ranking.csv`，由数据团队周更；**禁止**在 brooks 内自己造 ranking。
- **阈值改动留痕**：调阈值的 PR 必须在 [cta/report/change_log.md](../../../report/change_log.md) 写"为什么改"。
