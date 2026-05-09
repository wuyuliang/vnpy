# CTA 项目 — AI Agent 操作指引

> 给 Claude Code 等 AI 代理使用。读取顺序：本文 → `cta/README.md` →
> `cta/feature/FEATURES.md` / `cta/model/model.md` →
> `cta/report/change_log.md`（最近 3 条）。

---

## 1. 修改边界

只修改 `cta/**`。**禁止**修改：

- `vnpy/**` (vn.py 主框架)
- 仓库根的 `pyproject.toml` / `examples/` / `docs/` / `tests/` （非 cta）
- 任何 gateway / API 接入代码

如需新依赖（如 `vnpy_ctp`、`vnpy_ctabacktester`、`alphalens-reloaded`），**先和用户确认**，
不要自动 `pip install` 或修改 `pyproject.toml`。

---

## 2. Git 工作流

- **分支**：固定 `feature` 分支，**不要新建分支**。
- **Commit**：**不要自动 commit**；改完后只列出修改清单等用户审视后亲自提交。
- 若有 worktree 名称包含 `cta` 但实际不含 `cta/` 目录（已知 Claude Code 默认 worktree 行为），
  直接在 `/Users/wuyuliang/code/vnpy` 主仓 `feature` 分支编辑，不要尝试在 worktree 内创建 `cta/`。

---

## 3. 数据布局

```
cta/data/origin/day/{SYMBOL}.csv                          # akshare 日线
cta/data/origin/{interval}/{ALPHA_PREFIX}/{YYYY-MM-DD}.parquet  # tushare 分钟
    interval ∈ {minute, minute5, minute15, minute30, minute60}
    ALPHA_PREFIX = symbol 的字母前缀（CU0 → CU）
cta/data/feature/...                                      # 特征产物
cta/data/model_feature/...                                # 候选样本
```

字段统一：`symbol, exchange, interval, datetime, open, high, low, close, volume, open_interest, turnover`。

数据下载需要 `TUSHARE_TOKEN` 环境变量。

---

## 4. 关键模块

| 用途 | 模块路径 | 入口 |
|------|---------|------|
| 数据下载（akshare + tushare） | `cta/data_code/futures_downloader.py` | `FuturesDownloader` |
| 批量下载脚本 | `cta/data_code/download_all.py` | `python3 -m cta.data_code.download_all` |
| 数据校验 | `cta/data_code/validate.py` | `python3 -m cta.data_code.validate` |
| 主力分钟扩展 | `cta/data_code/expand_minute.py` | `python3 -m cta.data_code.expand_minute` |
| 特征引擎 | `cta/feature/` | `python3 -m cta.feature.run_all_features` |
| 模型管道 | `cta/model/model_pipeline.py` | 见 `cta/model/model.md` |
| 事件驱动回测引擎 | `cta/skills/data_backtest/event_driven_backtest.py` | `run_backtest()` |
| 回测 + 报告 runner | `cta/run/runner.py` | `run_event_driven_backtest()` |
| 综合 HTML 报告 | `cta/report/render/` | `write_html_report()` |
| 统一 CLI | `cta/cli.py` | `python3 -m cta.cli {backtest\|validate\|expand-minute}` |
| 策略实现 | `cta/strategy/` | 现有 4 类（Donchian/ATR/TightRange/PriceAction） |

---

## 5. 策略接口约定

现有策略（v1 接口，event-driven 回测专用）：

```python
class Strategy:
    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict]:
        """返回订单列表，订单字段：
        side ∈ {long, short, flat}
        order_type ∈ {market, limit, stop}
        lots, price?, symbol, multiplier, commission_rate, tick_size
        """
```

M2 起会**新增**一份 `vnpy_ctastrategy.CtaTemplate` 子类（接口 `on_bar(self, bar)` + 内部
`buy/sell/short/cover` 调用），用于 `vnpy_ctabacktester` / SimNow / 实盘。
两套接口共享纯函数式信号计算（位于 `cta/skills/`）。

---

## 6. 跑回测的标准流程

```bash
# (1) 用 CLI（推荐）
python3 -m cta.cli backtest \
    --strategy cta.strategy.demos:make_double_ma \
    --bars cta/data/origin/day/RB0.csv \
    --out-dir cta/report/backtest/$(date +%Y%m%d)_double_ma_rb0 \
    --title "DoubleMA / RB0 / day" \
    --limit-move-pct 0.07 \
    --liquidity-ratio 0.1

# (2) 在 strategy/tests/test_*.py 内（已有风格）
from cta.run.runner import run_event_driven_backtest
res = run_event_driven_backtest(strategy, bars, out_dir=..., title=..., cost_fn=estimate_cost)
```

输出目录约定：`cta/report/backtest/{YYYYMMDD}_{strategy}_{symbol}_{interval}_{tag}/`
内含 `report_*.html`、`metrics_*.json`、可选 `trade_log.csv`。

---

## 7. 测试与代码质量

- **TDD**：先写 / 改测试，再写实现。每个 `cta/{module}/` 都对应 `tests/` 子目录。
- **跑测试**：`python3 -m pytest cta/{module}/tests -q` 局部；`python3 -m pytest cta -q` 全量
  （注意 `cta/data/download_*_test.py` 需要 `TUSHARE_TOKEN`，CI 中可跳过）。
- **日志风格**：业务逻辑用 `loguru`/`logging.getLogger(__name__)`；不要 `print`，CLI 例外。
- **类型注解**：尽量加；不强制 mypy strict。
- **不写多余注释**：函数行为已被名字 + docstring 表达；不要写 "TODO"/"removed"/"used by X" 之类的注释。
- **不破坏向后兼容**：扩展 `EngineConfig` 等公共结构必须用 `default=None` 字段，不修改既有默认行为。

---

## 8. 实验记录

每次有意义的改动追加一条到 [`cta/report/change_log.md`](report/change_log.md) 顶部，格式参考最近条目：
- 标题包含日期、分支、一句话主题
- 章节：任务 / 范围（代码） / 验证 / 不在本次范围

回测产物落 `cta/report/backtest/`，命名 `{YYYYMMDD}_{strategy}_{symbol}_{interval}_{tag}/`。

---

## 9. 三阶段路线（当前位置）

```
[M1 离线评估] ─→ [M2 SimNow 仿真] ─→ [M3 实盘]
   ✓ 已交付            进行中              未启动
```

- **M1 已交付**：回测引擎涨跌停/流动性过滤、HTML 综合报告（含 Sortino / 蒙特卡洛 / 容量）、
  数据校验、主力分钟扩展工具、统一 CLI、本文 AGENTS.md。
- **M2 进行中**：把现有 4 个策略改造为 `CtaTemplate` 子类、接入 `vnpy_ctabacktester`、
  搭建 SimNow runner 与 parity_check 一致性校验。
- **M3 未启动**：风控规则、kill switch、守护进程、每日对账报告。

不要跳过阶段。M2 完成且仿真稳定 5 个交易日后再考虑 M3 实盘。
