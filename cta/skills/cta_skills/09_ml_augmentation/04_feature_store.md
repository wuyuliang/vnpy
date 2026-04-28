# 特征存储与版本化 / Feature Store

> 归属章节：`09_ml_augmentation/` · 前置：无 · 关联：`cta/feature/`、`cta/data/feature/`

## 1. Skill 定义

把所有 ML 会用到的**特征值**按 `symbol × interval × timestamp` 持久化，提供**时点一致**的读取 API，并保持**版本化 / 复现性**。仓库已有基础：`cta/feature/run_all_features.py`。

## 2. 解决什么问题

- 痛点 1：训练用 panda 现算 / 实盘用流式算，容易不一致。
- 痛点 2：特征口径变更后历史复盘结果改变。
- 痛点 3：特征重算成本大（180+ 特征 × 多年）。
- 增量价值：让 feature 成为可追溯、可对账的数据资产。

## 3. 适用市场 / 适用场景

- **品种类别**：全部
- **周期**：全部
- **行情状态前提**：任何
- **不适用场景**：只跑 1 次的纯实验（overhead 不值）

## 4. 核心指标

| 指标名 | 计算方式 | 参数建议 | 典型阈值 | 现有实现 |
|--------|----------|----------|----------|----------|
| feature_version | git hash + config hash | — | 写入 meta | TODO |
| feature_panel_path | `cta/data/feature/{interval}/{symbol}/` | — | — | `cta/feature/run_all_features.py` |
| as_of_loader | 返回 t 时点的 feature | — | 必须 left-join bar | `cta/feature/feature_loader.py` |
| freshness | 最新特征落后实盘时间 | — | ≤ 1 bar | TODO |
| schema_hash | 列集合 md5 | — | 变更需 bump version | TODO |

## 5. 常见策略映射

### 结构 A：分片 parquet
- 一个 `{symbol}/{interval}.parquet`，列 = 所有 pa_* + 衍生特征。
- 读取：`load_symbol_features(symbol, interval)` → DataFrame。
- 写入：`run_all_features.py` 多进程 + 断点续跑。

### 结构 B：在线特征
- 实盘：每根 bar 到来时增量计算同一套 feature，和离线对齐。
- 测试：随机抽 100 bar 对比离线 vs 在线值，diff < 1e-6。

## 6. 代码模块设计

```text
cta/feature/
├── run_all_features.py        # 已有
├── feature_loader.py          # 已有
├── feature_registry.py        # TODO: 注册 + 版本
├── online_feature.py          # TODO: 流式增量
└── feature_diff.py            # TODO: online vs offline 对账
```

```python
from dataclasses import dataclass
import pandas as pd

@dataclass
class FeatureMeta:
    name: str
    version: str
    interval: str
    depends_on: list[str]
    code_ref: str

def register_feature(meta: FeatureMeta) -> None:
    ...

def load_features_as_of(
    symbol: str,
    interval: str,
    ts: pd.Timestamp,
    cols: list[str] | None = None,
) -> pd.Series:
    """返回 ts 时点之前最新的 feature 行。"""
    ...

def verify_online_offline(
    symbol: str,
    interval: str,
    sample_size: int = 100,
) -> pd.DataFrame:
    """对账表。"""
    ...
```

## 7. 回测评估重点

- **必看指标（6）**：online vs offline diff 最大值 / 特征缺失率 / 加载延迟 / 磁盘占用 / 全品种重算耗时 / schema 变更频率
- **分层评估**：按 interval / 按品种
- **稳健性检验**：
  1. 随机抽样对账通过率 100%
  2. 断点续跑中断后一致性
  3. 新增 1 个特征不破坏旧版本读取

## 8. 常见错误

- 离线算时 `shift(-k)` 混入未来 → 训练泄漏
- 特征新增但 registry 没 bump → 复盘结果漂移
- parquet partition key 不对 → 读慢
- 实盘流式与离线 batch 累计误差（EMA 初值）

## 9. 迭代方向

- v1：parquet + registry
- v2：online feature 服务（Redis / sqlite）
- v3：特征依赖图 DAG（改一个特征自动决定重算哪些）
- v4：特征 AB（新老版本并行跑策略对比）

## 10. 与其他 Skills 的关系

- **依赖**：无（底层）
- **被依赖**：`09_ml_augmentation/01/02/03/05`、所有 ML 接入规则策略
- **互补**：`08_data_and_backtest_infra/05_trade_log_and_evaluation.md`（trade_log 里的 feature_snapshot）
