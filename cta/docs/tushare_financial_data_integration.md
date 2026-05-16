# CTA tushare 金融期货 + 指数数据接入设计

> 给 codex 落地用的设计文档。覆盖股指期货（IF/IH/IC/IM）、国债期货（T/TF/TS）和现货指数（上证/中证1000/沪深300）的下载、validate、macro feature join 全链路。

---

## 0. Context（为什么改）

当前 `cta/data_code/` 下载体系只覆盖 70 个商品期货（黑色/有色/化工/农产品/贵金属/能源）。要把策略扩展到金融期货（股指 + 国债）和宏观指数特征，需要：

1. **金融期货可交易接入**：沪深300（IF）/上证50（IH）/中证500（IC）/中证1000（IM）股指期货 + 十年（T）/五年（TF）/两年（TS）国债期货——加入 ranking csv，让现有 candidate/training/OOT pipeline 自动覆盖
2. **指数 reference 数据接入**：上证指数（000001.SH）/中证1000（000852.SH）/沪深300（000300.SH）作为**非交易品种**下载，存到独立目录，candidate 阶段 join 进所有品种作为宏观特征
3. **trading session 适配**：股指 09:30-11:30/13:00-15:00（无夜盘）、国债 09:15-15:15、商品有夜盘——validate 阶段需要按 cluster 分别校验

**用户已确认设计选择**：
- 金融期货 + 主要指数（不只是金融期货）
- 全频率（day + 60min/30min/15min/5min/min；指数仅 day）
- 金融期货主连用与商品一致的主力拼接（IF0/T0/...），复用 `pro.fut_mapping()`

---

## 1. As-Is 现状（来自 explore）

| 关注点 | 现状 | 文件:行 |
|--------|------|---------|
| 下载主入口 | `cta.data_code.download_all.main()` + CLI `--intervals / --max-rank / --workers / --rate-limit` | [download_all.py:483](../data_code/download_all.py:483) |
| Symbol 源 | `cta/feature/symbols_research_ranking.csv` 70 行，全是商品期货，**无金融期货** | [symbols_research_ranking.csv](../feature/symbols_research_ranking.csv) |
| tushare 客户端 | `RateLimiter`(令牌桶 450/min) + `_safe_retry` + 懒初始化 `_get_pro()` | [futures_downloader.py:119-227](../data_code/futures_downloader.py:119) |
| 已支持的 API | `pro.fut_mapping()` 主力映射、`pro.ft_mins()` 分钟数据 | [futures_downloader.py:303-378](../data_code/futures_downloader.py:303) |
| 存储路径 | 日线 csv：`cta/data/origin/day/{SYMBOL}.csv`；分钟 parquet：`cta/data/origin/{interval}/{prefix}/{YYYY-MM-DD}.parquet` | [futures_downloader.py:55-62](../data_code/futures_downloader.py:55) |
| 前缀 → cluster 映射 | **已预置** IF/IH/IC/IM→`index`、T/TF/TS→`bond` | [symbol_cluster_config.py:38-45](../config/symbol_cluster_config.py:38) |
| validate trading session | 仅查缺失日/重复/零成交，**无 session 分钟数校验** | [validate.py](../data_code/validate.py) |

**好消息**：cluster 前缀已就绪，roll_cost/limit_pct 已配置（index roll=0.02%、limit=10%；bond roll=0.01%、limit=2%）。只缺：(a) ranking 行、(b) 两个新 downloader、(c) macro feature join、(d) session-aware validate。

---

## 2. 设计原则与范围

### 2.1 范围

| 品种类别 | symbol | exchange | 频率 | 是否加 ranking | 用途 |
|---------|--------|----------|------|---------------|------|
| 股指期货主连 | IF0, IH0, IC0, IM0 | CFFEX | 全频率 | ✅ | 可交易，进训练/OOT |
| 国债期货主连 | T0, TF0, TS0 | CFFEX | 全频率 | ✅ | 可交易，进训练/OOT |
| 现货指数 | 000001.SH, 000852.SH, 000300.SH | SSE | 仅 day | ❌ | reference，作 macro feature join |

### 2.2 设计原则

1. **最大限度复用** `futures_downloader.py` 的 RateLimiter / retry / 存储路径约定
2. **指数与可交易品种分离存储**：reference 指数走 `cta/data/origin_index/`，避免被 candidate_training_dataset.py 错误地当作可交易品种生成开仓样本
3. **ranking 是品种入口的 single source of truth**：金融期货加进 ranking 后，**所有现有 pipeline 步骤（download/validate/feature/candidate/train/pool/group_pool）自动覆盖**，零代码改动到 model 层
4. **macro feature 是可选 join**：通过配置开关 `enable_macro_features=True` 启用，默认开但允许关掉以做 ablation
5. **trading session 按 cluster 分派**：用 `infer_symbol_cluster(symbol)` 路由到对应 session，validate 步骤按 session check

---

## 3. 文件改造清单

### 3.1 新建

| 文件 | 用途 |
|------|------|
| `cta/data_code/_tushare_utils.py` | 抽出 commodity 中可复用的 splice / chunk / write 工具 |
| `cta/data_code/financial_futures_downloader.py` | IF/IH/IC/IM/T/TF/TS 日线 + 分钟下载（与 commodity 同结构） |
| `cta/data_code/index_downloader.py` | 现货指数日线下载（仅 day） |
| `cta/config/trading_session_config.py` | 各 cluster 的交易时段表 + lookup |
| `cta/feature/index_reference_symbols.csv` | reference 指数清单（非 ranking） |
| `cta/feature/macro_feature.py` | 从 reference 指数构造 macro feature 列 |
| `cta/data_code/tests/test_financial_futures_downloader.py` | 单测 |
| `cta/data_code/tests/test_index_downloader.py` | 单测 |
| `cta/feature/tests/test_macro_feature.py` | 单测 |
| `cta/config/tests/test_trading_session_config.py` | 单测 |

### 3.2 修改

| 文件 | 修改点 |
|------|--------|
| [cta/data_code/download_all.py](../data_code/download_all.py) | 按 prefix 分派 commodity / financial / index downloader；新增 `--include-financial`、`--include-index` 开关（默认 True） |
| [cta/data_code/futures_downloader.py](../data_code/futures_downloader.py) | 把可复用函数迁移到 `_tushare_utils.py`，原 import 这些工具 |
| [cta/feature/symbols_research_ranking.csv](../feature/symbols_research_ranking.csv) | 追加 7 行：IF0/IH0/IC0/IM0/T0/TF0/TS0 |
| [cta/data_code/validate.py](../data_code/validate.py) | 引入 trading_session_config，按 cluster 选择校验规则 |
| [cta/model/feature/candidate_training_dataset.py](../model/feature/candidate_training_dataset.py) | 候选样本生成时 join macro feature（受配置开关控制） |
| [cta/feature/run_all_features.py](../feature/run_all_features.py) | 调用 `MacroFeatureBuilder.build() + save()`（一次写入，所有品种共用） |
| [cta/run.sh](../run.sh) | `step_data` 步骤新增 financial + index 下载子步骤；`INTERVALS_DOWN` 默认值适配 |

### 3.3 不修改

- 现有 [futures_downloader.py](../data_code/futures_downloader.py) 的对外 API（商品下载行为保持稳定，仅内部抽函数）
- 现有 [model_pipeline.py](../model/model_pipeline.py) / [candidate_training_dataset.py](../model/feature/candidate_training_dataset.py) 的训练逻辑（只在 candidate 阶段 join 一次 macro，对 model 透明）
- vnpy 主仓任何文件

---

## 4. 金融期货下载器（financial_futures_downloader.py）

### 4.1 接口

```python
from cta.data_code.futures_downloader import RateLimiter, _safe_retry, _get_pro
from cta.data_code._tushare_utils import (
    splice_continuous_from_mapping,
    chunked_minute_fetch,
    write_parquet_partitioned,
)

class FinancialFuturesDownloader:
    """复用 RateLimiter / retry，主连拼接逻辑与商品一致。"""

    SUPPORTED_PREFIXES: tuple[str, ...] = ("IF", "IH", "IC", "IM", "T", "TF", "TS")
    EXCHANGE: str = "CFFEX"
    TS_SUFFIX: str = ".CFX"  # tushare 中金所 ts_code 后缀

    def __init__(self, rate_limiter: RateLimiter, data_root: Path = CTA_ROOT / "data" / "origin"):
        self.rl = rate_limiter
        self.data_root = data_root
        self.pro = _get_pro()

    def fetch_mapping(self, prefix: str, start: str, end: str) -> pd.DataFrame:
        """复用 pro.fut_mapping(ts_code=f'{prefix}.CFX', start_date=..., end_date=...)
           输出每天主力合约的连续切换表。"""

    def fetch_continuous_day(self, prefix: str, start: str, end: str) -> pd.DataFrame:
        """按 mapping 拼接主力日线：
           - 对每个月份合约：pro.fut_daily(ts_code=f'{prefix}{YYMM}.CFX')
           - 按主力切换日切片拼接
           - 输出: trade_date, open, high, low, close, vol, amount, oi
           写到: cta/data/origin/day/{prefix}0.csv  (例: IF0.csv)
        """

    def fetch_continuous_minute(self, prefix: str, freq: str, start: str, end: str) -> None:
        """按月份合约逐天用 pro.ft_mins(ts_code=..., freq=freq, start_date=, end_date=)
           复用 _tushare_utils.chunked_minute_fetch + write_parquet_partitioned
           写到: cta/data/origin/{freq_norm}/{prefix}/{YYYY-MM-DD}.parquet
        """

    def fetch_all(self, prefix: str, intervals: list[str], start: str, end: str) -> None:
        """top-level: 对 prefix（如 'IF'）下载所有指定频率"""
```

### 4.2 与 commodity downloader 的差异

| 维度 | commodity (futures_downloader.py) | financial (financial_futures_downloader.py) |
|------|-----------------------------------|---------------------------------------------|
| tushare ts_code 后缀 | `.CZC` / `.SHF` / `.DCE` / `.INE` | 统一 `.CFX` |
| API | `pro.fut_mapping` + `pro.ft_mins` | 同（tushare 接口统一） |
| 主连拼接逻辑 | 已实现 | **复用** 同一拼接函数（抽到 _tushare_utils） |
| 夜盘 | 有 | 无（仅日盘） |
| 合约切换日规则 | 按 OI 主力 | 同（tushare 统一规则） |
| 月份合约命名 | `cu2501.SHF` | `IF2501.CFX` / `T2501.CFX` |
| 存储路径 | `cta/data/origin/{interval}/...` | **完全一致**（共用目录） |

### 4.3 复用的工具（_tushare_utils.py）

把现有 `futures_downloader.py` 里的下列函数抽到 `cta/data_code/_tushare_utils.py`：
- `splice_continuous_from_mapping(monthly_dfs, mapping_df) -> pd.DataFrame`
- `chunked_minute_fetch(pro, ts_code, freq, start, end, rate_limiter) -> pd.DataFrame`
- `write_parquet_partitioned(df, root_dir, prefix, interval)`

新旧两个 downloader 都 import 这套工具，避免代码复制。

---

## 5. 指数下载器（index_downloader.py）

### 5.1 接口

```python
class IndexDownloader:
    """现货指数：仅日线，存独立目录。
       tushare API: pro.index_daily(ts_code='000001.SH', start_date=, end_date=)
    """

    DEFAULT_SYMBOLS: tuple[tuple[str, str], ...] = (
        ("000001.SH", "上证指数"),
        ("000852.SH", "中证1000"),
        ("000300.SH", "沪深300"),
    )

    def __init__(self, rate_limiter, data_root: Path = CTA_ROOT / "data" / "origin_index"):
        self.rl = rate_limiter
        self.data_root = data_root
        self.pro = _get_pro()

    def fetch_index_day(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """单次拉取一支指数日线
           输出: trade_date, open, high, low, close, vol, amount, pct_chg
           写到: cta/data/origin_index/day/{ts_code_safe}.csv
                 其中 ts_code_safe = ts_code.replace('.', '_')  # 000001_SH.csv
        """

    def fetch_all(self, symbols: list[tuple[str, str]] | None = None, *, start: str, end: str) -> None:
        symbols = symbols or list(self.DEFAULT_SYMBOLS)
        for ts_code, _ in symbols:
            self.fetch_index_day(ts_code, start, end)
```

### 5.2 不下载分钟数据的理由

tushare 免费版 `pro.index_daily` 仅日线；分钟需 `pro_bar(asset='I')` 但限速更严。**Phase 1 仅做日线**，后续如要分钟再扩展（macro feature 用日线足够）。

### 5.3 存储路径分离

存到 `cta/data/origin_index/` 而非 `cta/data/origin/`，原因：
1. 现有 `validate.py` / `run_all_features.py` 扫描 `cta/data/origin/` 时不会把指数当成可交易品种
2. macro_feature.py 显式去 `origin_index/` 读取，逻辑清晰

---

## 6. ranking csv 新增条目

### 6.1 追加到 `cta/feature/symbols_research_ranking.csv`

| symbol | exchange | name | research_rank | tier | recommended_stage | suggested_cycle | reason | tier_comment |
|--------|----------|------|---------------|------|-------------------|----------------|--------|--------------|
| IF0 | CFFEX | 沪深300股指期货 | 71 | A | 全 | 60min/30min | 金融期货核心，流动性极佳，宏观关联强 | 股指主力 |
| IH0 | CFFEX | 上证50股指期货 | 72 | A | 全 | 60min/30min | 大盘蓝筹代表，与商品周期反向关联 | 股指主力 |
| IC0 | CFFEX | 中证500股指期货 | 73 | A | 全 | 60min/30min | 中盘成长，波动适中 | 股指主力 |
| IM0 | CFFEX | 中证1000股指期货 | 74 | B | 全 | 60min/30min | 小盘高 beta，2022 年新合约 | 股指扩展 |
| T0 | CFFEX | 十年国债期货 | 75 | A | 全 | day/60min | 利率核心，避险对冲首选 | 国债主力 |
| TF0 | CFFEX | 五年国债期货 | 76 | B | 全 | day/60min | 中端利率 | 国债扩展 |
| TS0 | CFFEX | 两年国债期货 | 77 | B | 全 | day/60min | 短端利率，与货币政策关联 | 国债扩展 |

### 6.2 排序原则

- research_rank 71-77 接续商品之后，让 `--max-rank` 默认能带上
- tier A 优先（IF/IH/IC/T 高流动）；IM/TF/TS 标 B
- suggested_cycle 仅作元数据，模型不读

### 6.3 新建 `cta/feature/index_reference_symbols.csv`

```csv
ts_code,file_safe_name,name,role,description
000001.SH,000001_SH,上证指数,macro_reference,A 股大盘风向标
000852.SH,000852_SH,中证1000,macro_reference,小盘成长指数
000300.SH,000300_SH,沪深300,macro_reference,大盘核心指数（与 IF 现货）
```

这个 csv 不进 ranking，仅供 IndexDownloader 和 macro_feature.py 读取。

---

## 7. trading_session_config.py

### 7.1 数据结构

```python
@dataclass(frozen=True)
class TradingSession:
    name: str
    sessions: tuple[tuple[str, str], ...]   # 每段 (start_hhmm, end_hhmm)，跨夜段允许 end < start
    has_night_session: bool

    def total_minutes(self) -> int: ...
    def is_within(self, dt: pd.Timestamp) -> bool: ...
    def expected_bar_count(self, freq_minutes: int) -> int: ...

# 4 种 session
COMMODITY_DAY_NIGHT = TradingSession(
    name="commodity_day_night",
    sessions=(("09:00","11:30"), ("13:30","15:00"), ("21:00","23:00")),  # 通用，部分品种夜盘到 02:30
    has_night_session=True,
)
INDEX_FUTURES = TradingSession(
    name="index_futures",
    sessions=(("09:30","11:30"), ("13:00","15:00")),
    has_night_session=False,
)
BOND_FUTURES = TradingSession(
    name="bond_futures",
    sessions=(("09:15","11:30"), ("13:00","15:15")),
    has_night_session=False,
)
INDEX_SPOT = TradingSession(
    name="index_spot",
    sessions=(("09:30","11:30"), ("13:00","15:00")),
    has_night_session=False,
)

# cluster → session 映射（用 symbol_cluster_config.infer_symbol_cluster）
CLUSTER_TO_SESSION: dict[str, TradingSession] = {
    "cluster_black": COMMODITY_DAY_NIGHT,
    "cluster_metal": COMMODITY_DAY_NIGHT,
    "cluster_chemical": COMMODITY_DAY_NIGHT,
    "cluster_agri": COMMODITY_DAY_NIGHT,
    "cluster_energy": COMMODITY_DAY_NIGHT,
    "cluster_precious": COMMODITY_DAY_NIGHT,
    "cluster_index": INDEX_FUTURES,
    "cluster_bond": BOND_FUTURES,
    "cluster_other": COMMODITY_DAY_NIGHT,
}

def get_session_for_symbol(symbol: str) -> TradingSession:
    cluster = infer_symbol_cluster(symbol)
    return CLUSTER_TO_SESSION.get(cluster, COMMODITY_DAY_NIGHT)
```

### 7.2 商品期货品种间差异

部分商品夜盘到 23:00，部分到 01:00 / 02:30。**第一版用统一 23:00**，validate 出现"凌晨多余 bar"时 warning 而非 error；后续按需细化（不在本次范围）。

### 7.3 validate.py 改造点

```python
# 现有 validate.py 加入 session 校验
from cta.config.trading_session_config import get_session_for_symbol

def validate_minute_bars(df, symbol, freq_minutes):
    session = get_session_for_symbol(symbol)
    # 每个交易日预期 bar 数
    expected = session.expected_bar_count(freq_minutes)
    actual = df.groupby(df['datetime'].dt.date).size()
    short_days = actual[actual < expected * 0.95]
    # ...
    # 检查 datetime 是否落在 session 内
    df['within_session'] = df['datetime'].apply(session.is_within)
    out_of_session = df[~df['within_session']]
    # ...
```

---

## 8. macro_feature.py

### 8.1 接口

```python
class MacroFeatureBuilder:
    """从 cta/data/origin_index/ 读取 reference 指数，构造 macro feature 表。
       一次构建后写到 cta/data/feature/macro/macro_daily.parquet，供所有品种 join。"""

    def __init__(self, index_root: Path = CTA_ROOT / "data" / "origin_index" / "day"):
        self.index_root = index_root

    def build(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """输出 DataFrame，index=trade_date，列:
           - macro_sse_close, macro_sse_ret_1d, macro_sse_ret_5d, macro_sse_ret_20d
           - macro_csi1000_close, macro_csi1000_ret_1d, macro_csi1000_ret_5d, ...
           - macro_csi300_close, macro_csi300_ret_*
           - macro_sse_vol_5d, macro_csi300_vol_20d  (rolling std of returns)
           - macro_spread_csi300_csi1000_ret_5d  (sector rotation proxy)
        """

    def save(self, out_path: Path = CTA_ROOT / "data" / "feature" / "macro" / "macro_daily.parquet") -> None: ...

    @classmethod
    def load(cls, path) -> pd.DataFrame: ...
```

### 8.2 候选样本 join

在 [candidate_training_dataset.py](../model/feature/candidate_training_dataset.py) 候选样本生成的尾端添加：

```python
if cfg.enable_macro_features:
    macro_df = MacroFeatureBuilder.load(MACRO_FEATURE_PATH)
    if "candidate_trade_date" not in candidates.columns:
        candidates["candidate_trade_date"] = (
            pd.to_datetime(candidates["datetime"], errors="coerce")
            .dt.strftime("%Y-%m-%d")
        )
    candidates = candidates.merge(
        macro_df.reset_index().rename(columns={"trade_date": "candidate_trade_date"}),
        on="candidate_trade_date", how="left"
    )
    # 缺失（仅 macro 数据范围外）填 NaN，由后续 dropna_required_columns 控制
```

### 8.3 配置开关

在 `cta/config/model_oot_eval_config.py`（或一个独立的 dataset config）加：
```python
enable_macro_features: bool = True
macro_feature_columns: tuple[str, ...] = (
    "macro_sse_ret_5d", "macro_csi300_ret_5d",
    "macro_csi300_vol_20d", "macro_spread_csi300_csi1000_ret_5d",
)
```

trade_filter / regime_classifier / mfe_mae / final_decision_stack 4 个 gate 都会自动把这些列加入候选 feature 集合（只要不在 leakage manifest 黑名单里）。

---

## 9. download_all.py 改造

### 9.1 dispatch 逻辑

```python
# 伪代码
def process_symbol(symbol: str, exchange: str, intervals: list[str], start: str, end: str):
    prefix = alpha_prefix(symbol)  # IF0 -> IF; CU0 -> CU
    if prefix in FinancialFuturesDownloader.SUPPORTED_PREFIXES:
        downloader = financial_futures_downloader
    else:
        downloader = futures_downloader  # 商品
    downloader.fetch_all(prefix, intervals, start, end)


def main():
    # 现有：从 ranking 读 commodity + financial（已统一到 ranking）
    symbols = read_ranking(max_rank=args.max_rank)

    # 新增：reference 指数（不在 ranking）
    if args.include_index:
        idx_downloader = IndexDownloader(rate_limiter=shared_limiter)
        idx_downloader.fetch_all(start=args.start, end=args.end)

    # 现有：分线程下载 ranking 品种
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        ex.map(lambda s: process_symbol(s, ...), symbols)

    # 新增：构建 macro feature
    if args.build_macro:
        builder = MacroFeatureBuilder()
        macro_df = builder.build()
        builder.save(macro_df)
```

### 9.2 新 CLI 参数

```bash
python -m cta.data_code.download_all \
    --intervals day minute60 minute30 minute15 minute5 minute \
    --max-rank 77 \
    --workers 4 \
    --rate-limit 450 \
    --include-financial \   # 默认 True
    --include-index \       # 默认 True
    --build-macro           # 默认 True（依赖 --include-index）
```

### 9.3 run.sh 改造

[cta/run.sh](../run.sh) 的 `step_data` 步骤：
```bash
step_data() {
    require_token
    require_ranking
    ensure_dirs
    log "data: downloading top-${TOP_N} (含金融期货+指数), intervals=[${INTERVALS_DOWN}]"
    run_step "01_data_download" \
        ${PYTHON} -m cta.data_code.download_all \
            --intervals ${INTERVALS_DOWN} \
            --max-rank "${TOP_N}" \
            --workers "${WORKERS}" \
            --rate-limit "${RATE_LIMIT}" \
            --include-financial \
            --include-index \
            --build-macro
}
```

把 `TOP_N` 默认值从 18 抬到 77（覆盖商品+金融期货），或单独维护 `INCLUDE_FINANCIAL_PREFIXES`。

---

## 10. 实施顺序（ROI 排序）

| Wave | 内容 | 文件 | 验证 |
|------|------|------|------|
| W1.1 | `_tushare_utils.py`：抽出 commodity downloader 中可复用的 splice / chunk 函数 | 新建 + 改 futures_downloader.py | 商品下载行为不变（单测） |
| W1.2 | `financial_futures_downloader.py` + 单测 | 1 新文件 | mock tushare 单测；本地实跑 IF0 一天数据 |
| W1.3 | ranking csv 加 7 行 | 修改 csv | grep IF0 / T0 能找到 |
| W2.1 | `index_downloader.py` + 单测 + index_reference_symbols.csv | 2 新文件 | 本地实跑 000001.SH 一年数据 |
| W2.2 | `trading_session_config.py` + 单测 | 1 新文件 | 单测覆盖 commodity / index / bond 三种 session |
| W3.1 | `download_all.py` dispatch + CLI 参数 | 改文件 | `python -m cta.data_code.download_all --include-financial --include-index --max-rank 77` 跑通 |
| W3.2 | `validate.py` session-aware 校验 | 改文件 | 对 IF0 / T0 / 000001.SH 校验报告无 false-positive |
| W4.1 | `macro_feature.py` + 单测 | 1 新文件 | 构建并保存 macro_daily.parquet |
| W4.2 | `candidate_training_dataset.py` join macro | 改文件 + 配置开关 | candidate 表新增 4-6 列 macro_* |
| W4.3 | `run_all_features.py` 调用 macro 构建 | 改文件 | step_feature 跑完后 macro_daily.parquet 存在 |
| W5 | run.sh 整合 + 跑通端到端 | 改 run.sh | `bash cta/run.sh data && bash cta/run.sh validate && bash cta/run.sh feature && bash cta/run.sh candidate` 全部成功 |

**关键依赖链**：
- W1.1 utils 抽取必须先于 W1.2（避免代码重复）
- W2.1 index 下载先于 W4.1 macro 构建
- W3.2 session validate 与下载解耦（独立可做）
- W4.2 candidate join 必须在 macro_daily.parquet 存在后才能跑

---

## 11. 验收清单（人工核查）

- [ ] `python -m pytest cta/data_code/tests/ cta/feature/tests/ cta/config/tests/ -v` 全绿
- [ ] `cta/data_code/financial_futures_downloader.py` / `index_downloader.py` / `trading_session_config.py` / `macro_feature.py` 4 个新文件存在
- [ ] [symbols_research_ranking.csv](../feature/symbols_research_ranking.csv) 包含 IF0/IH0/IC0/IM0/T0/TF0/TS0 共 7 行
- [ ] `cta/feature/index_reference_symbols.csv` 存在且含 3 个 ts_code
- [ ] 实跑 `bash cta/run.sh data`：
  - `cta/data/origin/day/IF0.csv` 等 7 个文件存在
  - `cta/data/origin/minute60/IF/2024-01-15.parquet` 等分钟数据存在
  - `cta/data/origin_index/day/000001_SH.csv` 等 3 个指数文件存在
- [ ] 实跑 `bash cta/run.sh validate`：
  - 商品期货报告无 session 误报
  - 股指/国债期货报告 expected_bar_count 与 actual 对得上（差异 <5%）
  - 指数报告仅 day，无 session 校验
- [ ] 实跑 `bash cta/run.sh feature`：
  - `cta/data/feature/macro/macro_daily.parquet` 存在且含 macro_sse_* / macro_csi*_* 列
- [ ] 实跑 `bash cta/run.sh candidate`：
  - candidate 输出 DataFrame 多出 4-6 列 macro_*（按配置）
  - IF0 / T0 / 商品品种都有 candidate 样本（行数非零）
- [ ] 实跑 `bash cta/run.sh train`：
  - IF0 / T0 模型训练成功，没有因 macro 列 NaN 导致 dropna 把所有行删掉
- [ ] OOT 报告 trade_details 中 symbol 列出现 IF0 / T0

---

## 12. 风险与回退

| 风险 | 应对 |
|------|------|
| tushare 限速 450/min 被超 | RateLimiter 已就位；新 downloader 共享同一 limiter 实例 |
| 金融期货历史数据起点晚（IM 仅 2022+） | 下载时按 prefix 设置不同 `start`：IF/IH/IC=2010-04, IM=2022-07, T=2013-09, TF=2013-09, TS=2018-08 |
| macro feature NaN 太多导致 dropna | 加 `min_macro_columns_present` 配置，缺一半以上 macro 列才丢弃，否则填 forward-fill |
| 商品品种 session 校验出现夜盘到 02:30 误报 | session.expected_bar_count 容差 5%；后续按品种细化 night_end |
| ranking 加新品种后默认 `TOP_N=18` 不会带 | 已调整默认 `TOP_N=77`；也可显式 `TOP_N=77 bash cta/run.sh data` |
| 指数 SH 后缀冲突 | 文件名用 `_` 替换 `.` （000001_SH.csv），避免某些 shell 处理 `.` 为分隔符 |
| 一键回退 | 删除新加的 7 行 ranking + 删除 `cta/data/origin_index/` + `enable_macro_features=False`，pipeline 退回原状 |

---

## 13. 给 codex 的执行指引

1. **不修改主 vnpy 仓代码**：所有改动在 `cta/` 下
2. **保持现有商品下载行为不变**：W1.1 utils 抽取后必须跑一次完整 commodity 下载 smoke 测试（用 1 个品种 1 天）
3. **代码风格参考**：[futures_downloader.py](../data_code/futures_downloader.py) 的 logger + RateLimiter 用法
4. **不引入新依赖**：复用 pandas / numpy / requests / tushare / loguru
5. **不自动 commit**（cta 项目工作流：feature 分支开发，禁止自动 commit）
6. **测试 framework**：unittest 风格，放在与源文件同级 `tests/` 目录
7. **mock tushare**：单测必须 mock `tushare.pro_api`，不要在 CI 里真打 API
8. **数据范围默认值**：start=2010-01-01（金融期货实际起点按 §12 risk 表配置），end=2025-12-31
9. **token 来源**：复用现有 `TUSHARE_TOKEN` 环境变量，downloader 在缺失时报错而非静默
10. **导入约定**：`from cta.data_code.financial_futures_downloader import FinancialFuturesDownloader`、`from cta.config.trading_session_config import get_session_for_symbol`

---

## 14. 不在本次范围

- A 股个股下载（接口 `pro.daily(ts_code='000001.SZ')`，方法与 index 类似但 symbol 数量大，单独立项）
- 可转债 / 期权数据
- 实时 tick 数据（需付费）
- 分钟级指数（需付费）
- 商品期货夜盘到 02:30 的细化 session 配置
