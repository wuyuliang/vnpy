# cta/docs/

## 主要做什么

**项目级长文档**目录：架构设计、口径约定、跨模块协议、code review 记录。和单文件顶部的 docstring / `cta/report/change_log.md` 互补——这里放"读完才能开始改代码"的体量内容。

## 关键文件

### 设计/口径
| 文件 | 内容 |
|---|---|
| [block_reason.md](block_reason.md) | OOT 执行期 + 候选期 25 种 `block_reason` 字面量全量 review、触发条件、Fix-A/B/C/D 完整 diff、§21 验收清单 |
| [portfolio_logic_design.md](portfolio_logic_design.md) | `cta/portfolio_logic/` 的整体设计：HTF gate / ranker / trailing / pyramid / risk_throttle / score_calibration 的协议与状态机 |
| [oot_output.md](oot_output.md) | OOT 评估产出文件清单与字段含义（`predictions.csv` / `details.csv` / `monthly.csv` / `summary.csv` / `position_lifetime.csv` 等） |
| [tushare_financial_data_integration.md](tushare_financial_data_integration.md) | 金融期货数据源对接：Tushare API 端点、字段映射、限速策略 |

### 重构/演进
| 文件 | 内容 |
|---|---|
| [refactor_long_files.md](refactor_long_files.md) | 拆分超 500 行文件的初版方案（W9 之前） |
| [refactor_long_files_v2.md](refactor_long_files_v2.md) | 拆分方案 v2（pipeline_orchestrator / pipeline_oot_evaluation 拆成 source.py.txt + loader 的路径） |

### Code Review
| 文件 | 范围 |
|---|---|
| [review/202605170735.md](review/202605170735.md) | 全量 review：trailing 方向、margin 双扣、weekly/monthly peak 重置 bug 等 critical |
| [review/2026051714.md](review/2026051714.md) | block_reason / htf_missing 修复 Fix-A~D 落地后 review |
| [review/review_claude_20260512.md](review/review_claude_20260512.md) ~ `20260516_v3.md` | 历史阶段性 review，按日期归档 |

## 详细过程

### 看什么 / 什么时候看
- 改 `cta/portfolio_logic/` 任何文件前 → 先看 [portfolio_logic_design.md](portfolio_logic_design.md)。
- 改 `cta/model/pipeline_oot_evaluation_source.py.txt` 或 `cta/portfolio_logic/interval_gate.py` 涉及 `block_reason` → 先读 [block_reason.md](block_reason.md) §1 总表 + §3 触发顺序。
- 加新 OOT 产出字段 → 先读 [oot_output.md](oot_output.md)，避免和既有字段语义冲突。
- 拆 500+ 行长文件 → 参考 [refactor_long_files_v2.md](refactor_long_files_v2.md) 里的 loader + `.txt` 方案。

### 怎么写一篇新文档
1. 文件名小写下划线，与主题相关。
2. 顶部用 markdown blockquote 写**面向谁、为什么写**。
3. 涉及代码引用一律用 markdown 链接 + `:line` 后缀，便于点击跳转。
4. Code review 一律放 `review/` 子目录，文件名格式 `YYYYMMDDHHMM.md` 或 `review_<source>_YYYYMMDD.md`。

## 注意事项

- **文档与代码必须一致**：改完代码后如果某条文档约定失效，要么改文档要么 revert 代码；不允许出现"文档说 A，代码做 B"。这是项目硬约束（见 `CLAUDE.local.md`：保持所有文档、代码、测试之间的一致性）。
- **markdown 链接行号**：随着文件被拆/重排，行号会漂移。尽量引用稳定的"函数名"或"section 标题"而非裸行号；若必须用行号，应在 review 任务里抽样校核。
- **review 文件不可删除**：历史 review 是项目"为什么走到今天这一步"的根本依据，禁止合并或重写历史 review。可以在新 review 里引用旧 review 修正其结论。
- **本目录不放代码**：脚本/工具应到对应模块下；本目录只接受 `.md`。
