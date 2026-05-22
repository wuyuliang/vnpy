# cta/utils/

## 主要做什么

**仓库级公共小工具**：和具体业务无关、被多个子模块共用的纯函数/小辅助。当前只放了随机种子统一入口；未来加 helper 时要先确认它无法归到任何业务模块再放这里，避免本目录膨胀成杂物筐。

## 关键文件

| 文件 | 作用 |
|---|---|
| [random_seed.py](random_seed.py) | `seed_all_from_env(env_var)`：从环境变量统一 seed numpy / random / torch（如装）三套 RNG；model_pipeline 入口先调它再做任何模型工作 |

## 详细过程

```python
from cta.utils.random_seed import seed_all_from_env

# pipeline / sim / live 入口最先做
used_seed = seed_all_from_env("CTA_GLOBAL_SEED")
# used_seed = None 表示 env 没设，跳过种子（保留原始随机性）
```

## 注意事项

- **不放业务逻辑**：任何带"特征 / 策略 / 模型 / 仓位"语义的工具都不应放本目录。
- **不依赖业务模块**：本目录的 import 应当只指向标准库 + numpy / pandas / torch 等基础库。如果要 `from cta.config import ...` 说明它不该在 utils。
- **测试**：跑 `pytest cta/utils/tests/ -v`；`test_random_seed.py` 同时校验 numpy / random / （可选）torch 三套都被 seed 了。
- **新增工具前先 grep**：很多"看似 utils"的函数其实已经存在于 `cta/feature/` 或 `cta/model/` 的内部 helpers；先 grep 再决定是否真要 promote 到本目录。
