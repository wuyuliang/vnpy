# cta/utils/tests/

## 主要做什么

[cta/utils/](..) 公共小工具的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_random_seed.py](test_random_seed.py) | `seed_all_from_env` 统一 seed numpy / random / (torch 如装) 三套 RNG，验证种子可复现、env 缺失时回退 |

## 注意事项

- **不放业务测试**：和 utils 本身一样，本目录测试只能 import 标准库 + numpy / pandas，不依赖任何 cta 业务模块。
- **种子可复现是硬约束**：`pipeline_orchestrator.main` 启动时第一步就调 `seed_all_from_env("CTA_GLOBAL_SEED")`，挂了相当于"运行结果不可复现"，必须立刻修。
- **跑测试**：`pytest cta/utils/tests/ -v`。
