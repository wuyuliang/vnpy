# cta/report/render/tests/

## 主要做什么

[cta/report/render/](..) 报告渲染层的单元测试。

## 关键测试

| 文件 | 覆盖 |
|---|---|
| [test_render.py](test_render.py) | metrics / plots / factor_analysis / monte_carlo / capacity / html_report 的输入输出冒烟与边界 |

## 注意事项

- **不依赖真实 csv**：测试用 tmp_path + 极小 fixture，不依赖 `cta/report/backtest/` 真实产物。
- **plot 测试 headless**：matplotlib 必须用 `Agg` backend；CI 环境无 X11。
- **跑测试**：`pytest cta/report/render/tests/ -v`。
