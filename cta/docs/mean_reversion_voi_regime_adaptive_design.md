# 均值回归 + 震荡上沿降仓 + VOI Regime Adaptive Momentum 设计文档

> 范围：`cta/strategy/`、`cta/feature/`、`cta/model/`、`cta/portfolio_logic/`、`cta/config/`
> 风格：与 [ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md) 对齐，snake_case，
> 默认 off，按 `(cluster, interval)` 灰度启用。

---

## §0 修订记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-05-21 | codex | 按默认 off 口径落地 `mean_reversion_range` setup、震荡上沿降仓 OOT 明细字段与 VOI 自适应动量特征，并补回归测试。 |
| 2026-05-21 | claude | 初稿。覆盖三个 regime-aware 模块：均值回归 setup、震荡上沿降仓、VOI 自适应动量因子。 |

---

## §1 概览与动机

### §1.1 三个互补的 regime-aware 思路

2024 INDEX day 牛市的 OOT 复盘暴露出一个核心矛盾：**模型与策略对 regime 的感知不足**。
当前已落地 `regime_gate`（pred_regime_label）和 `ma_cross_gate`（趋势排列），它们都属于
**入场前的方向过滤**。但策略层面仍有三块空白：

| 模块 | 解决问题 | 触发时机 |
|---|---|---|
| A. 震荡区间均值回归 setup | 趋势策略在震荡市连续假突破亏损 | 入场前（setup 阶段） |
| B. 震荡上沿提前降仓 | 趋势市中后段持仓 → 震荡市反转 → 回撤未及时锁定 | 持仓中（每日重估） |
| C. VOI Regime Adaptive Momentum | 模型只看单一窗口动量，高低波动期错配 | 特征/模型阶段 |

三个模块互补：A 是**进场新通道**（反向 setup），B 是**持仓退场加速**，C 是**因子层
自适应**。共享同一份 regime 标签来源（[feature/regime.py:39 compute_regime_features](../feature/regime.py)），
互不依赖、可独立 opt-in。

### §1.2 与现有 gate / filter 的关系

```
入场链路：
  baseline_setup_detection（含本文 §3 mean_reversion_range_setup）
    ↓
  trade_filter（gate 1）
    ↓
  regime_gate（pred_regime_label → block，gate 2）
    ↓
  ma_cross_gate（gate 3）
    ↓
  regime_short_filter（gate 4）
    ↓
  mfe_mae_gate（gate 5）
    ↓
  stacking_gate（gate 6）
    ↓
  enter

持仓链路：
  intrabar_stop_loss → trailing_stop → 本文 §4 oscillation_upper_band_position_taper → mfe_mae_realtime_exit

模型特征：
  feature/trend.py、feature/regime.py、feature/volume.py、本文 §5 voi_regime_adaptive_momentum
    ↓
  model_feature pipeline → 各训练模型
```

### §1.3 设计目标

1. **治本**：解决"震荡市追多被反向打穿"和"趋势末端不知何时减仓"两类系统性亏损。
2. **解耦**：三个模块独立，可单独启用，互不依赖。
3. **灰度**：默认 off，按 `(cluster, interval)` opt-in，复用 ma_cross gate 的 dict 模式。
4. **一致性**：OOT / sim / live 共用同一 cfg，避免离线训练与实盘漂移。
5. **可观测**：每个模块新增独立的 `signal_type` / `block_reason` / `exit_reason`，确保归因可查。

---

## §2 设计原则

### §2.1 OOT-only vs 全链路

| 模块 | 训练样本变化 | OOT 评估变化 | sim/live 变化 |
|---|---|---|---|
| A. mean_reversion_range_setup | **新增 setup_type 候选**（同时进入训练 + OOT） | 新 signal_type 出现 | 同步生效 |
| B. oscillation_upper_band_position_taper | 不变（只改持仓退场） | trade 列新增 `position_taper_*` | 同步生效 |
| C. voi_regime_adaptive_momentum | **新增特征列**（进入 model_feature） | 模型预测利用新特征 | 同步生效 |

A 和 C 会改变训练分布，必须遵守 `train_valid_oot` 时间切分；B 是**持仓后行为**，不影响样本筛选。

### §2.2 默认 off

```python
# cta/config/baseline_setup_config.py（或同一 cfg 体系）
use_mean_reversion_setup: bool = False
mean_reversion_enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

# cta/config/portfolio_logic_config.py
use_oscillation_upper_band_taper: bool = False
oscillation_taper_enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)

# cta/config/feature_pipeline_config.py
use_voi_regime_adaptive_momentum: bool = False
voi_momentum_enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

### §2.3 模块独立

任一模块的 P0 bug 不应阻塞其他模块的灰度。三个模块各自有单测、文档章节、灰度计划。

### §2.4 一致性：OOT / sim / live

所有模块必须在各自执行层复用同一 cfg 口径：

1. 模块 C 由 `VoiMomentumConfig` 控制 feature 生成，模型/OOT 只消费训练时同 schema 的特征；
2. 模块 B 进入 `PortfolioLogicConfig`，OOT 与共享持仓退场模拟都走同一 taper evaluator；
3. 模块 A 进入 setup config，candidate 生成和 baseline suite 都传同一
   `MeanReversionSetupConfig`。

如某个执行入口不读对应 cfg，需补 wire 并在测试中验证一致。

---

## §3 模块 A：震荡区间均值回归 setup（mean_reversion_range_setup）

### §3.1 信号假设

**前提**：处于 `range` regime（横盘震荡），价格围绕中枢做布朗运动。
**信号**：价格触及布林带上轨/下轨 + RSI 极值 + ADX 不强 → 反方向 setup。

数学上等价于：在震荡 regime 下，当价格偏离均值超过 N 个 std 时，反向回归概率
显著高于趋势延续概率。

### §3.2 适用品种与周期

| cluster | interval | 适用度 | 理由 |
|---|---|---|---|
| index | day | ★★★★☆ | 股指日线震荡市占比高（2014, 2018, 2022） |
| index | 60min | ★★★☆☆ | 噪音中等 |
| black | day | ★★★☆☆ | 黑色系节奏快，震荡期较短 |
| metal | day | ★★★★☆ | 有色震荡期长，宏观驱动主导 |
| precious | day | ★★★☆☆ | 贵金属（AU/AG）震荡期可观 |
| agri | day | ★★☆☆☆ | 农产品基本面驱动，震荡期间常含跳空 |
| chemical | day | ★★☆☆☆ | 化工与原油联动，震荡定义模糊 |
| bond | day | ★★★★☆ | 国债天然适合均值回归 |

### §3.3 数学定义

```python
# cta/feature/mean_reversion.py（新增）
def compute_mean_reversion_features(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    *,
    bb_window: int = 20,
    bb_std_mult: float = 2.0,
    rsi_window: int = 14,
    adx_window: int = 14,
) -> pd.DataFrame:
    """返回 mean_reversion_zscore / bb_upper / bb_lower / rsi / adx / mr_signal 等列。"""
    sma = close.rolling(bb_window).mean()
    std = close.rolling(bb_window).std(ddof=0)
    bb_upper = sma + bb_std_mult * std
    bb_lower = sma - bb_std_mult * std
    zscore = (close - sma) / std.where(std > 0, np.nan)
    rsi = _compute_rsi(close, rsi_window)
    adx = _compute_adx(high, low, close, adx_window)
    ...
```

**复合信号**：
- **short** 候选：`zscore >= 2.0` AND `rsi >= 70` AND `adx <= 20`
- **long** 候选：`zscore <= -2.0` AND `rsi <= 30` AND `adx <= 20`

`adx <= 20` 是"非趋势 / 弱趋势"过滤，避免在强趋势中做反向 setup。

### §3.4 entry / exit / stop 规则

| 类型 | short setup（触上轨） | long setup（触下轨） |
|---|---|---|
| entry | 次日开盘 / 触发当 bar 收盘后 | 同上 |
| target | sma（中枢回归） | sma |
| stop | bb_upper + 0.5 * atr | bb_lower - 0.5 * atr |
| max_holding_bars | 5（day） / 10（60min） | 同左 |

提前退出条件：
1. 价格回到 sma → 80% 平仓
2. 持仓中 regime 切换到 `trend_up` / `trend_down`（与 setup 反向）→ 立即平仓
3. ADX 上穿 25 → 立即平仓（trend 启动）

### §3.5 配置字段

```python
@dataclass(frozen=True)
class MeanReversionSetupConfig:
    use_mean_reversion_setup: bool = False
    bb_window: int = 20
    bb_std_mult: float = 2.0
    rsi_window: int = 14
    rsi_upper: float = 70.0
    rsi_lower: float = 30.0
    adx_window: int = 14
    adx_max: float = 20.0  # 超过则不出 setup
    require_range_regime: bool = True  # regime_label 必须 == 'range'
    target_atr_mult_stop: float = 0.5
    max_holding_bars: int = 5
    early_exit_on_regime_flip: bool = True
    early_exit_on_adx_breakout: float = 25.0
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

### §3.6 emit signal_type

新增 `signal_type = "mean_reversion_range"`，与现有 `breakout` / `pullback` / `momentum`
并列。**candidate.csv** 增列：
- `mr_zscore`、`mr_bb_upper`、`mr_bb_lower`、`mr_rsi`、`mr_adx`、`mr_signal_strength`

### §3.7 与现有 setup 的串行顺序

```
baseline_setup_detection._build_raw_setup_candidates
  ├─ breakout_setup（已有）
  ├─ pullback_setup（已有）
  ├─ momentum_setup（已有）
  └─ mean_reversion_range_setup（本节新增；require_range_regime）
```

冲突解决：同一 (symbol, datetime) 出现两个方向的 setup 时，优先级 `breakout > pullback >
mean_reversion_range`，且记录 `setup_priority_conflict` 标记便于 OOT 复盘。

---

## §4 模块 B：震荡上沿提前降仓（oscillation_upper_band_position_taper）

### §4.1 问题描述

趋势市切换到震荡市时，长仓在上沿不主动减仓 → 反转回中枢 → 回吐 30-50% 盈利。
传统 trailing stop 只在"绝对回撤"触发，但震荡市中价格在区间内来回，**触发不及时**。

解决思路：**在震荡 regime + 接近上沿** 时主动降仓，**优先于** trailing stop。
对 short 持仓对称（下沿提前回补）。

### §4.2 上沿 / 下沿计算

| 方法 | 公式 | 优点 | 缺点 |
|---|---|---|---|
| Bollinger | sma ± 2σ | 自适应波动 | 噪音敏感 |
| Donchian | rolling(N).max/min | 直观 | 滞后 |
| Keltner | sma ± k·ATR | 平滑 | 反应慢 |
| ATR Channel | anchor ± k·ATR | 灵活 | 锚点选择难 |

**推荐**：Donchian + Bollinger 双因子复合。**boundary** 定义为：

```python
upper_boundary = max(donchian_upper, bb_upper)  # 取更高的，更保守
lower_boundary = min(donchian_lower, bb_lower)
```

### §4.3 触发条件

```python
def should_taper(
    position: Position,
    bar: Bar,
    regime_label: str,
    *,
    cfg: OscillationTaperConfig,
) -> tuple[bool, float]:
    """返回 (是否降仓, 目标仓位比例 0.0~1.0)"""
    if not cfg.use_oscillation_upper_band_taper:
        return False, 1.0
    if regime_label not in cfg.taper_regimes:  # 默认 {'range', 'compression'}
        return False, 1.0
    upper, lower = compute_boundaries(bar, cfg)
    # 距离上沿的百分比距离
    distance_to_upper = (upper - bar.close) / max(upper - lower, 1e-9)
    if position.side == "long":
        if distance_to_upper <= cfg.upper_taper_trigger:  # 默认 0.20（距上沿 20%）
            target_ratio = _compute_taper_ratio(distance_to_upper, cfg)
            return True, target_ratio
    elif position.side == "short":
        distance_to_lower = (bar.close - lower) / max(upper - lower, 1e-9)
        if distance_to_lower <= cfg.lower_taper_trigger:
            target_ratio = _compute_taper_ratio(distance_to_lower, cfg)
            return True, target_ratio
    return False, 1.0
```

### §4.4 降仓策略

**线性降仓**（默认）：

| 距离上沿 (long) | 目标仓位 |
|---|---|
| > 50% | 100% |
| 50% → 20% | 100% → 60%（线性） |
| 20% → 0% | 60% → 20%（线性） |
| 触及/超过上沿 | 0%（清仓） |

**阶梯降仓**（可选）：

```python
if distance_to_upper >= 0.50:
    return 1.0
elif distance_to_upper >= 0.30:
    return 0.75
elif distance_to_upper >= 0.15:
    return 0.50
elif distance_to_upper >= 0.05:
    return 0.25
else:
    return 0.0
```

降仓动作发出 `exit_reason = "oscillation_upper_band_taper"`，与 hard_stop / trailing
/ time_stop 并列归因。

### §4.5 配置字段

```python
@dataclass(frozen=True)
class OscillationTaperConfig:
    use_oscillation_upper_band_taper: bool = False
    taper_regimes: tuple[str, ...] = ("range", "compression")
    upper_taper_trigger: float = 0.20
    lower_taper_trigger: float = 0.20
    boundary_method: str = "donchian_bb_max"  # or "donchian" / "bollinger" / "keltner"
    donchian_window: int = 20
    bb_window: int = 20
    bb_std_mult: float = 2.0
    taper_curve: str = "linear"  # or "stepwise"
    min_taper_step_pct: float = 0.10  # 单次最小降仓比例
    require_profit_to_taper: bool = True  # 仅在浮盈时降仓，避免割肉离场
    min_profit_pct_to_taper: float = 0.005
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

`require_profit_to_taper` 是防呆：浮亏时不应被本机制再降仓（让 stop_loss 管理）。

### §4.6 与 trailing stop / mfe_mae gate 对比

| 机制 | 触发条件 | 时机 | 目的 |
|---|---|---|---|
| hard_stop | 浮亏达到固定 % | 即时 | 防爆仓 |
| trailing_stop | 浮盈回撤超过阈值 | 即时 | 锁盈 |
| time_stop | 持仓超过 max_holding_bars | 周期边界 | 防长期占用 |
| mfe_mae_realtime_exit | MFE/MAE 比例 | 即时 | 模型预测的早退 |
| **oscillation_upper_band_taper** | range regime + 接近上沿 | 每日重估 | **预防震荡反转** |

oscillation_taper 是**主动预防**，其他是**被动响应**。串行顺序：先评估 taper（如果
触发就直接降仓），再评估 trailing / mfe_mae（针对剩余仓位）。

---

## §5 模块 C：VOI Regime Adaptive Momentum 因子

### §5.1 因子定义

VOI (Volatility / range Origin / Intraday) Regime Adaptive Momentum，是一个**自适应窗口
动量因子**，根据当前波动 regime 切换 lookback 窗口，并叠加日内位置和成交量两个过滤。

```
voi_momentum = adaptive_momentum × intraday_position_factor × volume_rank_factor
```

其中：
- `adaptive_momentum` = 5d return（高波动）or 20d return（低波动）
- `intraday_position_factor` = 2 × ((close - low) / (high - low) - 0.5)，∈ [-1, +1]
- `volume_rank_factor` = volume 在过去 60 日的百分位排名，∈ [0, 1]

### §5.2 vol regime 切换逻辑

```python
def classify_vol_regime(
    close: pd.Series,
    *,
    vol_window: int = 20,
    rank_window: int = 252,
    high_threshold: float = 0.70,
    low_threshold: float = 0.30,
) -> pd.Series:
    """返回 'high' / 'low' / 'mid' 标签序列。"""
    log_ret = np.log(close).diff()
    realized_vol = log_ret.rolling(vol_window).std() * np.sqrt(252)
    vol_rank = realized_vol.rolling(rank_window).rank(pct=True)
    regime = pd.Series("mid", index=close.index)
    regime.loc[vol_rank >= high_threshold] = "high"
    regime.loc[vol_rank <= low_threshold] = "low"
    return regime
```

`mid` 区域（30%-70% 百分位）建议**不发信号**（输出 0），避免过渡带噪音。

### §5.3 5-day vs 20-day momentum 公式

```python
def compute_adaptive_momentum(
    close: pd.Series,
    vol_regime: pd.Series,
    *,
    fast_window: int = 5,
    slow_window: int = 20,
) -> pd.Series:
    fast_mom = close.pct_change(fast_window)
    slow_mom = close.pct_change(slow_window)
    adaptive = pd.Series(np.nan, index=close.index)
    adaptive.loc[vol_regime == "high"] = fast_mom.loc[vol_regime == "high"]
    adaptive.loc[vol_regime == "low"] = slow_mom.loc[vol_regime == "low"]
    # mid 区域填 0（不发信号）
    adaptive.loc[vol_regime == "mid"] = 0.0
    return adaptive
```

### §5.4 intraday range position 过滤

```python
intraday_position = (close - low) / (high - low).where(high > low, np.nan)
intraday_position_factor = 2.0 * (intraday_position - 0.5)  # ∈ [-1, +1]
```

含义：
- close 接近 high（多头收盘强势）→ factor ≈ +1
- close 接近 low（空头收盘弱势）→ factor ≈ -1
- close 在区间中部 → factor ≈ 0

**乘法效应**：动量方向与日内方向同向时，因子放大；逆向时衰减甚至反号。

### §5.5 volume rank 过滤

```python
volume_rank_factor = volume.rolling(60).rank(pct=True)  # ∈ [0, 1]
```

要求**显著成交量**才确认动量：低成交量的动量被衰减。可选门限：

```python
voi_momentum_final = voi_momentum * volume_rank_factor
# 或硬门限：volume_rank < 0.3 → 输出 0
```

### §5.6 model_feature 接入点

新增文件 `cta/feature/voi_momentum.py`，导出：

```python
def compute_voi_features(
    df: pd.DataFrame,  # 含 close/high/low/volume
    *,
    cfg: VoiMomentumConfig,
) -> pd.DataFrame:
    """返回包含以下列的 DataFrame：
    - voi_vol_regime: str（"high" / "low" / "mid"）
    - voi_realized_vol_rank: float
    - voi_adaptive_momentum: float
    - voi_intraday_position: float
    - voi_intraday_position_factor: float
    - voi_volume_rank: float
    - voi_momentum_raw: float（动量 × 位置 × 成交量）
    - voi_momentum_signed_score: float（最终因子，乘以 vol_regime 是否激活）
    """
```

接入 `cta/feature/compute.py` 的通用特征计算主流程，并由
`cta/feature/run_all_features.py --voi-enabled-cells ...` 灰度输出到
`cta/data/feature/<interval>/<symbol>/`。

模型层面：
- `trade_filter_model` 增加 voi_momentum_signed_score 作为输入特征
- `regime_classifier_model` 可作为辅助特征
- 新模型 `voi_momentum_signal_model`（可选）：单独训练一个二分类，预测 voi_momentum 是否
  与未来 N 日收益符号一致

### §5.7 配置字段

```python
@dataclass(frozen=True)
class VoiMomentumConfig:
    use_voi_regime_adaptive_momentum: bool = False
    vol_window: int = 20
    rank_window: int = 252
    high_vol_threshold: float = 0.70
    low_vol_threshold: float = 0.30
    fast_momentum_window: int = 5
    slow_momentum_window: int = 20
    volume_rank_window: int = 60
    min_volume_rank: float = 0.0  # 0 表示不硬截断；可设 0.3
    enabled_by_cluster_interval: dict[str, bool] = field(default_factory=dict)
```

---

## §6 接口契约

### §6.1 setup 候选（模块 A）

```python
class MeanReversionRangeSetupGenerator:
    def __init__(self, cfg: MeanReversionSetupConfig): ...
    def generate(
        self,
        bars: pd.DataFrame,  # 含 close/high/low/volume + regime_label
    ) -> pd.DataFrame:
        """返回 candidate DataFrame，必含列：
        symbol, entry_datetime, side, signal_type='mean_reversion_range',
        mr_zscore, mr_rsi, mr_adx, regime_label_at_signal, target_price, stop_price
        """
```

### §6.2 持仓降仓（模块 B）

```python
class OscillationUpperBandTaper:
    def __init__(self, cfg: OscillationTaperConfig): ...
    def evaluate(
        self,
        position: Position,
        bar: Bar,
        regime_label: str,
    ) -> TaperDecision:
        """TaperDecision(should_taper: bool, target_ratio: float, exit_reason: str)"""
```

### §6.3 因子计算（模块 C）

```python
def compute_voi_features(df: pd.DataFrame, cfg: VoiMomentumConfig) -> pd.DataFrame: ...
```

---

## §7 配置示例（建议初始灰度）

### §7.1 第一周（保守，仅模块 C 单独灰度）

```python
VoiMomentumConfig(
    use_voi_regime_adaptive_momentum=True,
    enabled_by_cluster_interval={"index|day": True},
)
# 由 cta.feature.run_all_features --voi-enabled-cells 'index|day'
# 或 compute_single_symbol_features(..., voi_cfg=...) 消费；模块 A / B 仍然 off。
```

**为何先开 C**：纯特征工程，不改 setup 或 exit 逻辑，OOT 验证最容易回退。

### §7.2 第二周（开启模块 B）

```python
OscillationTaperConfig(
    use_oscillation_upper_band_taper=True,
    enabled_by_cluster_interval={
        "index|day": True,
        "bond|day": True,
    },
    require_profit_to_taper=True,
    min_profit_pct_to_taper=0.005,
)
```

**为何 B 在 A 之前**：B 只影响**已存在的 trade**的退场，最多让趋势 trade 提早 20%
退出。A 是新增 setup，会改变 candidate 分布，风险更大。

### §7.3 第三周（开启模块 A）

```python
MeanReversionSetupConfig(
    use_mean_reversion_setup=True,
    enabled_by_cluster_interval={
        "bond|day": True,
        "metal|day": True,
    },
)
```

**为何选 bond / metal**：国债天然适合均值回归，有色震荡期长。先在窄品种验证。

---

## §8 测试清单

### §8.1 模块 A 单测（`cta/strategy/tests/test_mean_reversion_range_setup.py`）

- `test_default_disabled` — `use_mean_reversion_setup=False` 不产生 candidate
- `test_short_signal_on_upper_band_overbought_in_range` — z=2.5 + rsi=75 + adx=15 + regime=range → short candidate
- `test_long_signal_on_lower_band_oversold_in_range` — z=-2.5 + rsi=25 + adx=15 + regime=range → long candidate
- `test_blocked_when_adx_high` — adx>25 即使 z 极值也不出 setup
- `test_blocked_when_not_in_range_regime` — regime=trend_up 不出 setup
- `test_only_applies_to_enabled_clusters` — `enabled_by_cluster_interval` 之外不出 setup
- `test_signal_type_emitted_correctly` — candidate 行 `signal_type=='mean_reversion_range'`
- `test_target_and_stop_price_consistent` — short 时 target<entry<stop；long 反之

### §8.2 模块 B 单测（`cta/portfolio_logic/tests/test_oscillation_upper_band_taper.py`）

- `test_default_disabled`
- `test_taper_long_near_upper_band_in_range` — long 持仓，距上沿<20% → target_ratio<1
- `test_taper_short_near_lower_band_in_range`
- `test_no_taper_in_trend_regime` — regime=trend_up 时不降仓
- `test_no_taper_when_loss` — `require_profit_to_taper=True` 且浮亏 → 不降仓
- `test_linear_curve` / `test_stepwise_curve` — 两种曲线产出正确比例
- `test_exit_reason_oscillation_upper_band_taper` — 触发时 exit_reason 正确
- `test_only_applies_to_enabled_clusters`

### §8.3 模块 C 单测（`cta/feature/tests/test_voi_regime_adaptive_momentum.py`）

- `test_default_disabled` — 不生成新特征列
- `test_classify_vol_regime_high_low_mid` — 三段分类正确
- `test_adaptive_momentum_uses_fast_in_high_vol` — high regime 段输出 5d return
- `test_adaptive_momentum_uses_slow_in_low_vol`
- `test_intraday_position_factor_range` — 输出 ∈ [-1, +1]
- `test_volume_rank_factor_range` — 输出 ∈ [0, 1]
- `test_voi_momentum_composite_sign_consistent_with_momentum`
- `test_mid_regime_outputs_zero`
- `test_warmup_period_returns_nan` — 前 rank_window 行为 NaN
- `test_only_applies_to_enabled_clusters` — 启用 dict 之外的 (cluster, interval) 不写列

### §8.4 端到端 A/B（`/tmp` 输出，不污染主 report）

```bash
# baseline：全 off
python -m cta.run.runner --cluster index --interval day \
  --start 2015-01-01 --end 2025-12-31 \
  --output-dir /tmp/voi_v0

# v1：仅模块 C
python -m cta.run.runner ... \
  --override use_voi_regime_adaptive_momentum=True \
    voi_momentum_enabled_by_cluster_interval='{"index|day":true}' \
  --output-dir /tmp/voi_v1

# v2：C + B
python -m cta.run.runner ... \
  --override use_voi_regime_adaptive_momentum=True \
    use_oscillation_upper_band_taper=True \
    voi_momentum_enabled_by_cluster_interval='{"index|day":true}' \
    oscillation_taper_enabled_by_cluster_interval='{"index|day":true}' \
  --output-dir /tmp/voi_v2

# v3：C + B + A
python -m cta.run.runner ... \
  --override use_voi_regime_adaptive_momentum=True \
    use_oscillation_upper_band_taper=True \
    use_mean_reversion_setup=True \
    voi_momentum_enabled_by_cluster_interval='{"index|day":true}' \
    oscillation_taper_enabled_by_cluster_interval='{"index|day":true}' \
    mean_reversion_enabled_by_cluster_interval='{"index|day":true}' \
  --output-dir /tmp/voi_v3
```

**验收指标**：
- v1 vs v0：因子 IC 显著（|IC|>0.03），net_pnl 不恶化
- v2 vs v1：max_drawdown 下降 ≥10%，winrate 不显著恶化
- v3 vs v2：新增 mean_reversion_range trade 净盈利，胜率 ≥45%

---

## §9 灰度策略

### §9.1 滚动启用日历

| 周次 | 启用模块 | cluster\|interval | 退出条件 |
|---|---|---|---|
| W1 | C only | index\|day | 因子 IC < 0.01 持续 1 周 → 回退 |
| W2 | C + B | index\|day, bond\|day | max_drawdown 恶化 >10% → 回退 B |
| W3 | C + B + A | bond\|day, metal\|day（A 单独窄启） | A trade 累计 net_pnl < 0 → 回退 A |
| W4 | 全模块扩展到 black\|day | — | 同上 |

### §9.2 全局退出条件

任一簇任一模块出现以下任一情况，立即回退该 (cluster, interval, module) 组合：
- net_pnl 恶化 >10%（vs 前 30 日基准）
- max_drawdown 恶化 >15%
- 模块特有 trade 胜率 <40%
- 出现非预期的 `exit_reason` / `signal_type` 异常分布

---

## §10 已知限制

### §10.1 模块 A（mean reversion）

- 震荡 → 趋势的切换点：可能在突破前夜出 setup，导致反向被打穿。`adx_max=20`
  和 `early_exit_on_adx_breakout=25` 是缓冲。
- 跳空：日线市场开盘跳空会让 stop 失效，需要 day_open_protection 机制（待设计）。
- 与 breakout setup 互斥：同一品种同一时点不能同时是 mean_reversion 和 breakout，
  当前用优先级解决，未来可考虑 ensemble 投票。

### §10.2 模块 B（oscillation taper）

- regime label 准确性：依赖 `_infer_regime_label` 的准确率，错判 trend 为 range 会
  导致趋势市过早降仓。建议同步 emit `regime_label_confidence` 列。
- 部分品种区间不明显（如 NI / SN）：boundary 计算噪音大。建议在 §8.4 验证后单独
  设白名单。
- 与 trailing stop 重叠：可能同时触发两个 exit，需要明确归因优先级（建议 taper 在
  trailing 之前评估）。

### §10.3 模块 C（VOI momentum）

- 252 日 rank window 需要 ~1 年 warmup，新合约前期为 NaN。
- intraday position 在 day bar 上信号较弱（高低差小），在 60min / 30min 上更有效，
  但当前主要回测在 day 级别，跨周期适用性需 §8.4 验证。
- mid regime 输出 0 可能让因子大段时间为 0，导致模型训练时该特征 IC 不显著；可考虑
  改为线性插值（fast/slow 加权平均）替代硬截断。
- volume rank 在合约切换日附近会有跳变（主力合约换月），建议接 `cta/data_code/` 的
  连续合约处理逻辑。

### §10.4 跨模块

- 训练样本分布变化：模块 A 和 C 同时启用时，candidate 数量和特征分布同时变化，需要
  完整重训 trade_filter / regime_classifier 等下游模型，灰度时按 §9.1 滚动启用避免
  叠加效应。
- 配置膨胀：本设计新增约 25 个字段，建议拆分为独立的 sub-config dataclass（如
  `MeanReversionSetupConfig` / `OscillationTaperConfig` / `VoiMomentumConfig`），
  在 `OotEvaluationConfig` / `PortfolioLogicConfig` 中以组合方式聚合。

---

## §11 实施 TODO（按优先级）

| 优先级 | 任务 | 文件 | 单测 |
|---|---|---|---|
| P0 | 模块 C 因子计算 | `cta/feature/voi_momentum.py` | `cta/feature/tests/test_voi_regime_adaptive_momentum.py` |
| P0 | 模块 C cfg | `cta/config/voi_momentum_config.py` | — |
| P0 | 模块 C 接入 feature pipeline | `cta/feature/compute.py` / `cta/feature/run_all_features.py` | `cta/feature/tests/test_voi_regime_adaptive_momentum.py` |
| P1 | 模块 B 降仓 logic | `cta/portfolio_logic/oscillation_taper.py` | `cta/portfolio_logic/tests/test_oscillation_upper_band_taper.py` |
| P1 | 模块 B 接入共享持仓退场链路 | `cta/portfolio_logic/trailing_exit.py` | `cta/portfolio_logic/tests/test_oscillation_upper_band_taper.py` |
| P1 | 模块 B 接入 OOT 明细 | `cta/model/oot/pipeline_oot_evaluation.py` | `cta/model/tests/test_model_pipeline_part02.py` |
| P2 | 模块 A setup generator | `cta/strategy/mean_reversion_range_setup.py` | `cta/strategy/tests/test_mean_reversion_range_setup.py` |
| P2 | 模块 A 接入 baseline_setup_detection | `cta/strategy/baseline_setup_detection.py` | — |
| P2 | 模块 A signal_type 更新 | `cta/config/baseline_skill_suite_config.py` | `cta/strategy/tests/test_mean_reversion_range_setup.py` |
| P3 | 文档同步：block_reason.md / change_log.md | `cta/docs/*` | `cta/run/tests/test_docs_sync.py` |

---

## §12 不变量

- **默认 off**：三模块默认 `use_*=False` 且 `*_enabled_by_cluster_interval={}`，
  现有回测口径完全不变
- **canonical**：新增 `signal_type` / `exit_reason` 必须进入对应的 canonical 集合
  和 Literal 类型
- **OOT/sim/live 一致**：三层共用同一 cfg
- **不污染训练分布**：A 和 C 灰度启用时必须**完整重训**下游模型，不允许新旧数据混用
- **TDD**：先写测试再写实现；本文 §8 是测试 spec，可作为黑盒 spec 直接执行
- **文档先行**：本文是 spec，实施前任何接口变更需同步更新本文 §0 修订记录

---

## §13 风险与回退

| # | 风险 | 应对 |
|---|---|---|
| R1 | 模块 A 在趋势市误出反向 setup → 持续亏损 | `adx_max=20` + `early_exit_on_adx_breakout=25` + 灰度 |
| R2 | 模块 B 在弱趋势市过早降仓 → 错失行情 | `require_profit_to_taper=True` + regime 限定 |
| R3 | 模块 C mid regime 输出 0 导致因子断层 | §10.3 已提改用线性插值方案 |
| R4 | 三模块同时启用时下游模型未重训 → 分布偏移 | §9.1 滚动启用 + 强制重训 checkpoint |
| R5 | regime_label 计算 bug → 三模块全部失效 | 单测覆盖 regime_label 计算；模块内 fallback pass-all |
| R6 | 配置字段膨胀，user 配置错误 | dataclass `__post_init__` 校验 + 配置示例文档 |

---

## §14 参考实现链路

- 现有 regime gate（pred 路径）：[oot_gates.py:apply_regime_gate](../model/oot/oot_gates.py)
- 现有 MA-cross gate：[oot_gates.py:apply_ma_cross_gate](../model/oot/oot_gates.py)
- 现有 regime label 计算：[feature/regime.py:compute_regime_features](../feature/regime.py)
- 现有 setup 候选生成：[strategy/baseline_setup_detection.py](../strategy/baseline_setup_detection.py)
- 现有 ATR / 均线 / 范围特征：[feature/trend.py](../feature/trend.py) / [feature/volatility.py](../feature/volatility.py)
- 现有 trailing stop / hard stop / time stop：[portfolio_logic/](../portfolio_logic/)
- 同款"按 (cluster, interval) 灰度" 模式：[ma_cross_regime_aware_design.md](./ma_cross_regime_aware_design.md)
- block_reason / exit_reason canonical：[block_reason.md](./block_reason.md)

---

## §15 后续扩展（v2 思考）

1. **Ensemble setup**：在 (symbol, datetime) 同时存在 breakout 和 mean_reversion
   候选时，引入元学习器决定执行哪个。
2. **Regime confidence**：当前 regime_label 是硬标签，未来扩展成 soft probability，
   模块 A/B 的触发可以做概率加权。
3. **Multi-timeframe coherence**：day 级别 range 但 60min 级别 breakout 时，
   决策权重应如何分配。
4. **Adaptive thresholds**：rsi_upper / adx_max / zscore_threshold 当前是常数，
   可考虑按 cluster / 历史分位动态调整。
5. **VOI 扩展到 microstructure**：5min / tick 级别引入真正的 order imbalance
   （bid_vol vs ask_vol）。
