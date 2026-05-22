# cta/tests/

## 主要做什么

**仓库级测试目录**：只放跨子模块、跨包的顶层 smoke test 与 CLI 测试。具体单元测试一律放回各自模块的 `tests/` 子目录（如 `cta/model/tests/`、`cta/strategy/tests/`），这是仓库硬约定。

## 关键文件

| 文件 | 作用 |
|---|---|
| [test_cli.py](test_cli.py) | 端到端 CLI smoke：`python -m cta.model.model_pipeline --help` / `python -m cta.data_code.download_all --help` 等不报错 |

## 详细过程

跑全仓测试的入口建议：
```bash
pytest cta/ -x            # 短路 fail，本地最常用
pytest cta/ --maxfail=5   # 多看几个失败
pytest cta/tests/ -v      # 只跑顶层
```

## 注意事项

- **新增测试不放这里**：除非测试逻辑跨 ≥2 个模块且无法归属任何一个子模块，否则一律放回对应模块的 `tests/`。这条来自 [cta/strategy/readme.md](../strategy/readme.md) 的"测试目录约定"。
- **CLI smoke 是底线**：每次重构 import 路径或 `__main__` 入口后必须先跑这里。
- **不允许慢测试**：本目录的测试要在 5 秒内跑完；任何 fixture 加载实数据/磁盘 IO 都应该挪到对应模块的 tests/ 下。
