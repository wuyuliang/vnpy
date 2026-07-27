# 创业板 ETF 状态覆盖与 EMA 基线图形设计

## 1. 目标

在现有真实回测目录中新增：

```text
stock/etf/output/20260727_chuangyeban_regime_overlay/charts/
```

生成两张可复现 PNG：

```text
0001_159915_SZ_易方达创业板ETF_状态覆盖.png
0002_159915_SZ_易方达创业板ETF_EMA基线.png
```

图形沿用
`stock/etf/output/20260719_chuangyeban_optimized/charts/` 的深色标题栏、左侧指标卡片、
右侧周 K、日 K、成交量和买卖点风格。状态覆盖图增加 3 日状态背景与 1 日/3 日分数
轨道；EMA 基线图不显示状态信息。

同时生成：

```text
charts/index.csv
charts/render_summary.json
```

## 2. 实现路径

采用复用现有 PIL 绘图器的方案：

- 扩展 `stock/etf/render_trade_charts.py`，增加可选策略指标和状态图层。
- 新增 `stock/etf/regime_overlay_charts.py`，负责两张图的输入整理、命名、索引和审计。
- 修改 `stock/etf/run_regime_overlay_backtest.py`，在原事务暂存目录中生成图形。
- 不使用 Matplotlib 重写整套样式，不对生成后的 PNG 做坐标不可靠的后处理。

现有 `render_symbol_card` 默认行为保持不变，其他 ETF 图形不因本功能改变。

## 3. 数据来源

两张图统一使用本轮回测在内存中的因果连续行情：

```text
symbol
datetime
open
high
low
close
volume
```

报告区间固定为：

```text
2017-08-14 至 2026-07-20
```

状态覆盖图使用：

```text
RegimeOverlayResult.signals
RegimeOverlayResult.trades
RegimeOverlayResult.positions
RegimeOverlayResult.summary
```

EMA 基线图使用独立运行结果：

```text
RegimeOverlayResult.ema_result.signals
RegimeOverlayResult.ema_result.trades
RegimeOverlayResult.ema_result.positions
EMA 独立绩效指标
```

禁止用状态覆盖层的成交替代 EMA 成交，也禁止从最终状态仓位反推 EMA 买卖点。

## 4. 状态图

### 4.1 结构

状态覆盖图尺寸允许高于参考图，以避免压缩状态轨道。布局为：

```text
深色标题栏
左侧策略与交易指标
右上：周 K + 周成交量 + 买卖点 + 3 日状态背景
右下：日 K + 日成交量 + 买卖点 + 3 日状态背景
日线底部：score_1d / score_3d 双分数轨道
```

周状态按 `W-FRI` 聚合，每周使用该周最后一条实际行情的 `score_3d/state_3d`。日状态
直接使用该交易日记录，不按自然日填充，不跨停牌日补值。

### 4.2 五档背景

背景由 `score_3d` 决定，边界与策略完全一致：

| 分数 | 状态 | 背景色 |
|---|---|---|
| `[-3,-2]` | 趋势向下 | 红 |
| `(-2,-1)` | 震荡向下 | 浅橙 |
| `[-1,1]` | 无趋势 | 灰 |
| `(1,2)` | 震荡向上 | 浅绿 |
| `[2,3]` | 趋势向上 | 绿 |

背景使用低透明度，不遮挡 K 线、成交量和买卖点。连续相同状态合并为视觉区间，避免
逐根绘制边框。

### 4.3 分数轨道

日线面板绘制：

```text
score_1d：蓝线
score_3d：橙线
```

纵轴固定为 `[-3,3]`，绘制 `-2/-1/1/2` 四条阈值虚线和 0 中线。轨道不使用未来值、
平滑或插值；每个点只对应该执行日实际使用的上一行情日收盘预测。

### 4.4 左侧指标

至少显示：

```text
总收益
最大回撤
Sharpe
年化单边换手
交易数
佣金
滑点
0%/50%/100% 目标仓位占比
期末持仓状态
```

## 5. EMA 基线图

EMA 图保持参考图的标准结构：

```text
左侧策略与交易指标
右上周 K、成交量和 EMA 独立买卖点
右下日 K、成交量和 EMA 独立买卖点
```

不显示状态背景或状态分数。标题和副标题明确写出 `EMA 基线`，避免与状态覆盖图混淆。

左侧至少显示：

```text
总收益
最大回撤
Sharpe
年化单边换手
交易数
佣金
滑点
期末持仓状态
```

## 6. 索引与审计

`charts/index.csv` 每张图一行：

```text
rank
strategy
symbol
name
trade_count
buy_count
sell_count
is_open
total_return
max_drawdown
sharpe
annual_one_way_turnover
image_path
```

排序固定为：

```text
1. regime_overlay
2. ema_only
```

`charts/render_summary.json` 至少记录：

```text
schema_version
symbol
report_start
report_end
rendered_images
expected_images
image_files
state_background_driver
score_tracks
```

JSON 严格禁止 `NaN` 和 `Infinity`。

## 7. 事务发布

图形在回测输出 UUID 暂存目录的 `charts/` 中生成。发布前检查：

- 两张预期 PNG 均存在且非空。
- 两张图片可由 PIL 重新打开，尺寸符合设计。
- `index.csv` 恰好两行且图片路径存在。
- `render_summary.json` 为严格 JSON 且 `rendered_images=2`。

任一图形失败时，本轮整个回测输出不替换旧目录。成功后图形与 CSV/JSON 回测产物一起
原子发布。

## 8. 测试

新增或扩展测试覆盖：

- `score_3d` 在 `-3/-2/-1/0/1/2/3` 的五档背景边界。
- 周状态只取该周最后一条实际行情，不使用下一周数据。
- 分数轨道保持原始 `score_1d/score_3d`，不平滑、不回填。
- 状态图和 EMA 图分别使用覆盖层成交与独立 EMA 成交。
- 两张图片文件名、顺序、尺寸和非空内容。
- `index.csv` 两行且策略顺序固定。
- `render_summary.json` 严格合法。
- 图形生成失败时旧输出目录保持不变。
- 现有无状态 ETF 图形测试继续通过，默认视觉行为不变。

## 9. 验收

真实重跑后人工打开两张 PNG，确认：

- 中文名称、策略名称和日期范围清晰。
- K 线、成交量和买卖点可辨认。
- 状态背景不会遮挡价格。
- 1 日/3 日分数颜色、阈值和图例清楚。
- EMA 图没有混入状态成交或状态图层。
- 输出目录中恰有两张目标 PNG、`index.csv` 和 `render_summary.json`。

所有代码、测试和设计只提交到 `feature` 分支，不合并主分支。真实图片保留在本机输出
目录，不强制加入 Git。
