# ETF 轮动成交复盘图设计

## 目标

为 `2026-05-17` 至 `2026-07-17` ETF 轮动真实回测中实际成交过的每只 ETF 生成一张静态 PNG 复盘图。图形参考 `cta/analysis/20260628_bigger_01_allowlist_trade_charts/charts/` 的卡片风格，只包含周 K、日 K 和各自成交量，不包含小时 K。

## 输入

- 日线：`stock/etf/data/20260717_last2months_live/etfs.csv`
- ETF 元数据：`stock/etf/data/20260717_last2months_live/metadata.csv`
- 成交：`stock/etf/output/20260717_last2months_live/trades.csv`
- 持仓：`stock/etf/output/20260717_last2months_live/positions.csv`

只渲染 `trades.csv` 中出现的 symbol。当前预期为 26 只 ETF、76 笔成交。

## 输出

输出目录固定为：

```text
stock/etf/output/20260717_last2months_live/charts/
```

目录包含：

- `{rank:04d}_{symbol}_{safe_name}.png`：每只 ETF 一张图。
- `index.csv`：代码、中文名、基金类型、成交数、买入数、卖出数、已实现盈亏、首末成交日和图片路径。
- `render_summary.json`：输入代码数、成功图片数、跳过数和缺失数据明细。

排序按每只 ETF 的卖出成交 `realized_pnl` 合计降序，同分时按 symbol 升序。`rank` 从 1 开始。文件名中的中文名仅保留中文、英文、数字、连字符和下划线，其他字符替换为下划线。

## 画布布局

使用 Pillow 直接渲染，保持参考图的米白背景、深色页眉、圆角卡片和低对比网格。

- 画布：1680 x 1000 像素。
- 页眉：显示 `symbol`、中文名称、基金类型和报告日期范围。
- 左栏：宽约 430 像素，显示成交与绩效摘要。
- 右上：Weekly K 和周成交量。
- 右下：Daily K 和日成交量。

左栏字段：

- 首笔成交日、末笔成交日。
- 买入次数、卖出次数、总成交次数。
- 累计买入金额、累计卖出金额。
- 累计佣金、累计滑点成本、累计已实现盈亏。
- 最后报告日是否仍持仓、持仓份额和持仓成本。
- 卖出主原因去重列表。

## 数据窗口

- 周线使用该 ETF 缓存中的全部日线，即最早可到 `2025-12-29`，最晚为缓存末日 `2026-07-16`。
- 周线按 `W-FRI` 聚合：open=first、high=max、low=min、close=last、volume=sum。
- 日线从报告首个交易日前 20 个有效交易日起，到缓存最后一个有效交易日止。
- 买卖点只使用本次 `trades.csv` 中的真实成交，不标注 warmup 区间的任何推导信号。

## 蜡烛和成交量

- A 股习惯：上涨 K 线红色，下跌 K 线绿色，平盘灰色。
- 影线、实体和成交量柱使用同一涨跌颜色。
- 价格轴使用当前面板内 low/high 范围并留出上下边距，不强制从零开始。
- 成交量从零开始，面板标注 `Vol`。
- 横轴显示首尾日期及少量均匀日期刻度，避免密集文字。

## 买卖点

- 买入：蓝色竖线、上三角、标签 `B1`、`B2` 等。
- 卖出：橙色竖线、下三角、标签 `S1`、`S2` 等。
- 日线使用实际成交日期，标记价格使用 `fill_price`。
- 周线将成交日映射到对应 `W-FRI` 周 K，标记价格仍使用实际 `fill_price`。
- 同一 symbol 的买卖编号分别按成交日期和原始成交顺序递增。
- 同日同方向存在多笔成交时合并为一个标签，例如 `B1/B2`，并使用加权平均成交价。
- 标记超出可见窗口时不绘制，但保留在左侧成交统计中。

## 代码结构

新增：

- `stock/etf/render_trade_charts.py`
- `stock/etf/tests/test_trade_charts.py`

渲染器提供：

- 日线标准化和周线聚合函数。
- 日线窗口选择函数。
- 每 symbol 成交汇总和标记映射函数。
- 单图渲染函数。
- 批量渲染、索引和摘要输出函数。
- CLI 入口，参数支持输入路径、输出目录、`--limit` 和 `--overwrite`。

不修改 CTA 渲染器，不引入 Matplotlib 或 mplfinance。

## 异常处理

- 成交代码缺少中文名称时使用 symbol 作为名称，并在摘要记录。
- symbol 有成交但无日线时不生成 PNG，在 `index.csv` 标记 `missing_daily_data`，继续处理其他 symbol。
- 日线不足以聚合周线时仍渲染已有周 K。
- 输出文件已存在且未传 `--overwrite` 时不重复渲染，并在摘要记录 `existing`。
- 无有效成交时生成只有表头的 `index.csv` 和合法 `render_summary.json`，不报成功图片。

## 测试和验收

自动测试必须覆盖：

- `W-FRI` 周线 OHLCV 聚合正确。
- 日线窗口恰好包含首个报告交易日前 20 个有效交易日。
- 买卖点按日期和方向编号，周线日期映射正确。
- 中文名称能显示并生成安全稳定文件名。
- 一只 ETF 的最小输入可生成非空、尺寸为 1680 x 1000 的 PNG。
- 缺失日线只影响对应 symbol。

全量验收：

- `charts/` 下 PNG 数等于有成交且有日线的唯一 ETF 数，预期 26。
- `index.csv` 行数等于成交唯一 ETF 数，预期 26。
- 每张图都有周 K、日 K、两组成交量、symbol、中文名和至少一个买卖标记。
- 抽查累计盈亏最高、最低和最终仍持仓的图，确认标签不裁切、不重叠到无法辨认。
- 运行 `python3 -m pytest stock/etf/tests -q`、`python3 -m ruff check stock/etf` 和 Python 编译检查。
