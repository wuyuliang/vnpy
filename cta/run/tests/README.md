# cta/run/tests/

## 主要做什么

[cta/run/](..) 批量跑批入口测试 + 仓库级硬约束（文件长度、文档同步、CLI 健康）。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_runner.py](test_runner.py) | 单一回测 runner：装配 config → 调 backtest engine → 落盘 |
| [test_multi_runner.py](test_multi_runner.py) | 多任务批量 runner：任务切分、断点续跑、串/并行 |
| [test_cta_backtester.py](test_cta_backtester.py) | 顶层入口 `cta_backtester.py` 的 CLI 与调用链 |
| [test_run_script_actions.py](test_run_script_actions.py) | shell 脚本调用 runner 的行为（参数透传、退出码） |
| [test_docs_sync.py](test_docs_sync.py) | **关键 README / docs 必须存在**（防文档随重构失踪） |
| [test_no_500plus_files.py](test_no_500plus_files.py) | **任何 cta/ 下 .py 文件超 500 行报错**（[CLAUDE.local.md](../../../CLAUDE.local.md) 硬约束） |
| [conftest.py](conftest.py) | 跑批 fixture（临时目录、清理、mock backtest engine） |

## 注意事项

- **`test_no_500plus_files.py` 是硬底线**：任何 `.py` 文件 ≥ 500 行立即 fail。`cta/model` 的历史 `*_source.py.txt` 已删除，pipeline 必须继续保持正常 `.py` 小模块拆分。
- **`test_docs_sync.py` 校验关键文档存在**：新加 README / docs 时要把它加到这里的"必须存在"列表里。
- **断点续跑**：`test_multi_runner.py` 校验中途 kill 重启会跳过已完成的子任务。
- **跑测试**：`pytest cta/run/tests/ -v`；改任何 Python 文件之前/之后建议先跑 `pytest cta/run/tests/test_no_500plus_files.py` 验证不超限。
