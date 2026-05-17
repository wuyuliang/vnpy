# cta/docs/review/

## 主要做什么

**Code Review 归档目录**。每一次重大变更或定期巡检产出的 review 报告按时间序落到这里，作为项目"为什么改成现在这样"的不可变历史。

## 关键文件

| 文件 | 范围 | 类型 |
|---|---|---|
| [202605170735.md](202605170735.md) | 全量 review，发现 trailing 方向反、margin 双扣、weekly/monthly peak 不重置等 critical bug | 全量巡检 |
| [2026051714.md](2026051714.md) | block_reason / htf_missing 修复（Fix-A/B/C/D）落地后 review | 单 PR review |
| [review_claude_20260512.md](review_claude_20260512.md) | 早期阶段性巡检（pipeline 拆分前） | 历史 |
| [review_claude_20260516.md](review_claude_20260516.md) ~ `v3.md` | 同日多版本迭代 review | 历史 |

## 详细过程

### 文件命名约定
- **时间点 review**：`YYYYMMDDHHMM.md`（精确到小时分钟，避免同日多次冲突）。
- **特定 reviewer 标识**：`review_<reviewer>_YYYYMMDD[_vN].md`，例如 `review_claude_20260516_v3.md`。

### 文档结构推荐
1. **Header / Scope**：审查范围、提交时间、变更清单（含 `file:line` 链接）。
2. **Executive Summary**：Critical / High / Medium / Low / Info 计数表。
3. **逐项 finding**：每项写"问题—影响—修法建议—优先级"四段。建议附 file:line 链接，必要时贴 ~10 行代码上下文。
4. **验收结果**：跑过的测试列表 + 通过/失败计数。
5. **Follow-up**：未在本次范围但需要后续 ticket 跟进的事项。

### 何时开新 review
- 单 PR ≥ 200 行核心代码改动 → 强烈建议合入前写一篇。
- 跨模块联动 / 涉及风控或仓位 / 涉及 OOT 口径 → 必须。
- 周末/版本切换的"全仓检视"→ 时间点 review。

## 注意事项

- **历史 review 不可删除/合并/重写**：它们记录的是"当时的判断与依据"，后续若结论变了应在**新 review** 里引用并修正，不要回头改旧文件。
- **行号链接会漂移**：本目录 review 的 markdown 链接大量用 `file:line`，随重构会失准。**不允许**写脚本批量改行号；要么在新 review 里附"勘误：line 已变为 X"，要么在引用前先 grep 当前位置。
- **找特定 finding**：用 `grep -rn "<关键字>" cta/docs/review/` 比按文件名翻更快。
- **review 与 change_log 的分工**：
  - `cta/report/change_log.md`：每次 commit / 实验跑批的一行式记录（what + when + where）。
  - 本目录：每次重大变更的多段 review 报告（why + how + verification + follow-up）。
- **不要把 review 当 design doc 用**：设计/口径请放到 `cta/docs/` 一级目录（`portfolio_logic_design.md` / `block_reason.md` 等）。
