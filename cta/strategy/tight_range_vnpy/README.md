# tight_range_vnpy

这是一个**独立存放在 `vnpy/cta/tight_range_vnpy` 目录下**的自定义 CTA 项目，**不修改 vn.py 原生源码**。

目标：
1. 遍历目录 `vnpy_futures_csv/` 下每个品种的**日线 csv**
2. 生成 `tight range breakout` 所需天级别特征
3. 将特征保存到独立 SQLite：`tight_range_features.sqlite`
4. 可选：把日线 bar 导入 vn.py 自身数据库
5. 使用 vn.py 的 `BacktestingEngine` 直接运行 `TightRangeBreakoutStrategy`
6. 交易信号、成交动作继续写回同一个特征 SQLite 的 `signal_events` 表

---

## 一、目录结构

建议在腾讯云目录里放成这样：

```bash
/root/
├── vnpy/
│   └── cta/
│       └── tight_range_vnpy/
│           ├── tight_range_vnpy/
│           ├── scripts/
│           ├── data/
│           ├── examples/
│           └── README.md
└── day/
    ├── RB99.csv
    ├── I99.csv
    └── M99.csv
```

---

## 二、安装依赖

先确保你已经装好了：

- vnpy
- vnpy_ctastrategy
- pandas
- numpy

如果是你现有的 vn.py 环境，通常只要激活环境后再确认：

```bash
pip install pandas numpy
```

---

## 三、设置 PYTHONPATH

因为这个项目独立放在 `vnpy/cta/tight_range_vnpy`，为了让脚本能 `import tight_range_vnpy`，请先执行：

```bash
cd /root/vnpy/cta/tight_range_vnpy
export PYTHONPATH=/root/vnpy/cta/tight_range_vnpy:$PYTHONPATH
```

你也可以写进 `~/.bashrc`：

```bash
export PYTHONPATH=/root/vnpy/cta/tight_range_vnpy:$PYTHONPATH
```

---

## 四、准备 CSV

CSV 放在：

```bash
/root/day/
```

每个品种一个文件，格式参考：

```csv
symbol,exchange,datetime,open,high,low,close,volume
RB99,SHFE,2020-01-02,3560,3588,3550,3579,123456
RB99,SHFE,2020-01-03,3582,3601,3570,3595,133210
```

更多说明见：

```bash
examples/sample_daily_csv_columns.md
```

---

## 五、先生成特征到 SQLite

这一步是你现在最关心的：**遍历目录 `vnpy_futures_csv`，给每个品种生成天级别特征并存到 SQLite**。

运行：

```bash
cd /root/vnpy/cta/tight_range_vnpy
export PYTHONPATH=/root/vnpy/cta/tight_range_vnpy:$PYTHONPATH

python scripts/build_features_to_sqlite.py \
  --csv-dir /root/day \
  --db-path /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite
```

成功后会输出类似：

```bash
[OK] features saved | RB99.csv | rows=1200
[OK] features saved | I99.csv  | rows=1200
[DONE] files=2 total_rows=2400 db=/root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite
```

检查特征表：

```bash
python scripts/check_sqlite_features.py \
  --db-path /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite
```

查看某个品种：

```bash
python scripts/check_sqlite_features.py \
  --db-path /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite \
  --symbol RB99 \
  --exchange SHFE
```

---

## 六、把日线 CSV 导入 vn.py 数据库

如果你要让 vn.py 回测直接读取这些日线数据，需要再把 CSV 导入 vn.py 的 bar 数据库。

运行：

```bash
cd /root/vnpy/cta/tight_range_vnpy
export PYTHONPATH=/root/vnpy/cta/tight_range_vnpy:$PYTHONPATH

python scripts/import_daily_csv_to_vnpy_db.py \
  --csv-dir /root/day
```

注意：
- 此脚本调用的是 `vnpy.trader.database.get_database()`
- 也就是写入你当前 vn.py 配置好的数据库（默认通常是 SQLite）
- CSV 里 `exchange` 必须是 vn.py 可识别枚举，比如 `SHFE/DCE/CZCE/INE/GFEX/CFFEX`

---

## 七、在 CTA 回测里加载 `TightRangeBreakoutStrategy`

这里**不需要把策略文件复制到 vn.py 原生源码目录**，而是直接用脚本导入策略类并运行回测。

核心回测脚本：

```bash
scripts/run_backtest.py
```

运行示例：

```bash
cd /root/vnpy/cta/tight_range_vnpy
export PYTHONPATH=/root/vnpy/cta/tight_range_vnpy:$PYTHONPATH

python scripts/run_backtest.py \
  --feature-db /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite \
  --vt-symbol RB99.SHFE \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --rate 0.0002 \
  --slippage 1 \
  --size 10 \
  --pricetick 1 \
  --capital 1000000
```

这段脚本内部做的事情是：

```python
from vnpy_ctastrategy.backtesting import BacktestingEngine
from tight_range_vnpy.strategy import TightRangeBreakoutStrategy

engine = BacktestingEngine()
engine.set_parameters(...)
engine.add_strategy(
    TightRangeBreakoutStrategy,
    {
        "feature_db_path": "/root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite",
        "entry_score": 68,
        "min_expected_rr": 1.2,
        "use_fixed_size": 1,
        "fixed_size": 1,
        "trailing_atr_multiplier": 2.0,
        "stop_atr_multiplier": 1.0,
    },
)
engine.load_data()
engine.run_backtesting()
engine.calculate_result()
engine.calculate_statistics()
engine.show_chart()
```

这就是“在 CTA 策略里加载 `TightRangeBreakoutStrategy`”的完整可跑版本。

---

## 八、策略逻辑说明

策略类：

```bash
tight_range_vnpy/strategy.py
```

逻辑是：

1. 启动时从 `feature_db_path` 读取当前 `symbol.exchange` 的离线特征
2. 每个日线 bar 到来时，用 `trade_date` 查当天离线特征
3. 若满足：
   - `candidate_flag == 1`
   - `opportunity_score >= entry_score`
   - `expected_rr_rule >= min_expected_rr`
   则发多单
4. 持仓后按：
   - `硬止损 = entry - stop_atr_multiplier * ATR`
   - `跟踪止损 = 持仓后最高价 - trailing_atr_multiplier * ATR`
   二者取更高者
5. 若当日机会分显著变差（默认 `< 45`），则直接平仓
6. 所有 setup / filled / weak_exit 事件写入 SQLite `signal_events`

---

## 九、SQLite 表说明

### 1）daily_features
离线特征表，主键：

```text
(symbol, exchange, trade_date)
```

保存：
- tight range 结构特征
- breakout 特征
- 背景特征
- 规则分
- 最终 `opportunity_score`
- 机会等级 `A/B/C/D`

### 2）signal_events
策略运行时写入：
- BUY_SETUP
- BUY_FILLED
- SELL_FILLED
- WEAK_EXIT

---

## 十、最小跑通顺序

直接按这个顺序来：

### 第1步：生成特征

```bash
python scripts/build_features_to_sqlite.py \
  --csv-dir /root/day \
  --db-path /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite
```

### 第2步：导入 vn.py bar 数据库

```bash
python scripts/import_daily_csv_to_vnpy_db.py \
  --csv-dir /root/day
```

### 第3步：运行回测

```bash
python scripts/run_backtest.py \
  --feature-db /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite \
  --vt-symbol RB99.SHFE \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --rate 0.0002 \
  --slippage 1 \
  --size 10 \
  --pricetick 1 \
  --capital 1000000
```

### 第4步：检查信号

```bash
python scripts/check_sqlite_features.py \
  --db-path /root/vnpy/cta/tight_range_vnpy/data/tight_range_features.sqlite
```

---

## 十一、为什么这样设计

你要求：
- 项目单独放 `vnpy/cta` 下
- 不和 vn.py 原生代码耦合
- 腾讯云上直接跑
- 先离线把天级别特征算好并存 SQLite

这套结构正好满足：

1. **研究与策略解耦**：特征先离线算，不在回测时重复扫目录
2. **和 vn.py 弱耦合**：只通过官方包导入 `BacktestingEngine` / `CtaTemplate`
3. **部署简单**：腾讯云上目录清晰，SQLite 单文件好迁移
4. **后续易升级**：以后你加 XGBoost 分数，只要在 `features.py` 或新增 `model_scoring.py` 改写 `opportunity_score` 即可

---

## 十二、后续升级建议

你跑通后，下一步建议是：

1. 把 `rule score` 替换成 XGBoost 模型分
2. 把 `单品种日线` 扩展到 `多品种日线`
3. 再扩到 `60min`
4. 增加组合层仓位管理，不只单策略单品种

