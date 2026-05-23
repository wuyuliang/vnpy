"""Dataset preparation helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import (
    BASELINE_SIGNAL_TYPES,
    BacktestConfig,
    DEFAULT_GENERIC_COLUMNS,
    LABEL_MAE_PENALTY,
    LABEL_THRESHOLD,
    _WalkForwardWindow,
    _build_pooled_feature_df_impl,
    _build_training_feature_table_with_auto_fallback,
    generate_candidate_opportunities,
    load_bars,
    logger,
    normalize_interval,
    np,
    pd,
    prepare_master_feature_frame,
    resolve_exchange,
)
from cta.config.baseline_skill_suite_config import TRAINING_FEATURE_COLUMNS
from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.strategy.baseline_candidate_gen import _infer_regime_label, _simulate_candidate_execution_path
from cta.strategy.cross_sectional_momentum_rotation import (
    CrossSectionalMomentumRotation,
    SIGNAL_TYPE as CROSS_SECTIONAL_SIGNAL_TYPE,
    _is_rebalance_day,
)

def _build_synthetic_candidate(symbol: str, exchange: str, interval: str, start_date: str, end_date: str, periods: int=400) -> pd.DataFrame:
    rng = np.random.default_rng(20260426)
    n = max(int(periods), 120)
    dt = pd.date_range(start=pd.Timestamp(start_date), end=pd.Timestamp(end_date), periods=n)
    x1 = rng.normal(0.0, 1.0, size=n)
    x2 = rng.normal(0.0, 1.0, size=n)
    x3 = rng.normal(0.0, 1.0, size=n)
    signal_types = np.array(BASELINE_SIGNAL_TYPES, dtype=object)
    signal_code = np.arange(n, dtype=int) % len(signal_types)
    signal_type = signal_types[signal_code]
    status_draw = rng.uniform(0.0, 1.0, size=n)
    candidate_status = np.where(status_draw < 0.65, 'filled', np.where(status_draw < 0.85, 'not_triggered', 'filtered'))
    is_executed = (candidate_status == 'filled').astype(int)
    is_filtered = (candidate_status == 'filtered').astype(int)
    is_triggered = (candidate_status == 'filled').astype(int)
    raw_mfe = np.maximum(0.0, 0.9 * x1 + 0.4 * x2 + rng.normal(0.0, 0.25, size=n))
    raw_mae = np.maximum(0.0, -0.5 * x1 + 0.5 * x3 + rng.normal(0.0, 0.25, size=n))
    future_mfe_atr = np.where(is_executed == 1, raw_mfe, 0.0)
    future_mae_atr = np.where(is_executed == 1, raw_mae, 0.0)
    label = np.where(is_executed == 1, (raw_mfe - LABEL_MAE_PENALTY * raw_mae > LABEL_THRESHOLD).astype(int), 0)
    side = np.where(x2 >= 0, 'long', 'short')
    regime = np.where(x1 > 0.7, 'trend_up', np.where(x1 < -0.7, 'trend_down', 'range'))
    base_price = 3500.0 + 20.0 * x1
    return pd.DataFrame({'symbol': str(symbol).upper(), 'exchange': str(exchange).upper(), 'interval': str(interval), 'datetime': dt, 'signal_datetime': dt, 'signal_type': signal_type, 'side': side, 'entry_i': np.arange(n, dtype=int), 'horizon_i': np.arange(n, dtype=int) + 20, 'entry_price': base_price, 'stop_price': base_price - np.where(side == 'long', 20.0, -20.0), 'future_mfe_atr': future_mfe_atr, 'future_mae_atr': future_mae_atr, 'label_class': label, 'regime_label': regime, 'candidate_status': candidate_status, 'is_executed': is_executed, 'is_filtered': is_filtered, 'is_triggered': is_triggered, 'filtered_reason': np.where(is_filtered == 1, 'synthetic_filter', ''), 'feature_close': base_price, 'feature_volume': 2000.0 + 300.0 * np.abs(x2), 'feature_atr14': 20.0 + 2.0 * np.abs(x3), 'feature_trend_score': x1, 'feature_breakout_score': x2, 'feature_setup_quality': 0.6 * x1 + 0.4 * x2})

def _ensure_training_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if 'datetime' not in out.columns:
        raise KeyError('candidate frame missing datetime')
    out['datetime'] = pd.to_datetime(out['datetime'], errors='coerce')
    out = out.dropna(subset=['datetime']).copy()
    if 'candidate_status' not in out.columns:
        out['candidate_status'] = 'filled'
    out['candidate_status'] = out['candidate_status'].astype(str).replace({'': 'filled'}).fillna('filled')
    if 'is_executed' not in out.columns:
        out['is_executed'] = (out['candidate_status'] == 'filled').astype(int)
    out['is_executed'] = pd.to_numeric(out['is_executed'], errors='coerce').fillna(0).astype(int)
    if 'is_filtered' not in out.columns:
        out['is_filtered'] = out['candidate_status'].astype(str).str.startswith('filtered').astype(int)
    out['is_filtered'] = pd.to_numeric(out['is_filtered'], errors='coerce').fillna(0).astype(int)
    if 'is_triggered' not in out.columns:
        out['is_triggered'] = out['is_executed']
    out['is_triggered'] = pd.to_numeric(out['is_triggered'], errors='coerce').fillna(0).astype(int)
    if 'filtered_reason' not in out.columns:
        out['filtered_reason'] = ''
    out['filtered_reason'] = out['filtered_reason'].astype(str).fillna('')
    if 'label_class' not in out.columns:
        if 'label_win' in out.columns:
            out['label_class'] = pd.to_numeric(out['label_win'], errors='coerce').fillna(0).astype(int)
        else:
            out['label_class'] = 0
    out['label_class'] = pd.to_numeric(out['label_class'], errors='coerce').fillna(0).astype(int)
    if 'future_mfe_atr' not in out.columns:
        out['future_mfe_atr'] = pd.to_numeric(out.get('label_mfe_atr', 0.0), errors='coerce')
    if 'future_mae_atr' not in out.columns:
        out['future_mae_atr'] = pd.to_numeric(out.get('label_mae_atr', 0.0), errors='coerce')
    if 'atr_warmed' not in out.columns:
        out['atr_warmed'] = 1
    out['atr_warmed'] = pd.to_numeric(out['atr_warmed'], errors='coerce').fillna(1).astype(int)
    mfe_raw = pd.to_numeric(out['future_mfe_atr'], errors='coerce')
    mae_raw = pd.to_numeric(out['future_mae_atr'], errors='coerce')
    warm_mask_arr = out['atr_warmed'].astype(int).to_numpy() == 1
    out['future_mfe_atr'] = np.where(warm_mask_arr, mfe_raw.fillna(0.0), mfe_raw)
    out['future_mae_atr'] = np.where(warm_mask_arr, mae_raw.fillna(0.0), mae_raw)
    if 'regime_label' not in out.columns:
        if 'feature_trend_dir' in out.columns:
            trend_raw = out['feature_trend_dir']
        else:
            trend_raw = pd.Series([0.0] * len(out), index=out.index)
        trend_dir = pd.to_numeric(trend_raw, errors='coerce').fillna(0.0)
        out['regime_label'] = np.where(trend_dir > 0, 'trend_up', np.where(trend_dir < 0, 'trend_down', 'range'))
    out['regime_label'] = out['regime_label'].astype(str).replace({'': 'range'}).fillna('range')
    if len(np.unique(out['label_class'])) < 2:
        mfe = pd.to_numeric(out['future_mfe_atr'], errors='coerce').fillna(0.0)
        mae = pd.to_numeric(out['future_mae_atr'], errors='coerce').fillna(0.0)
        exec_mask = out['is_executed'].astype(int) == 1
        warm_mask = out['atr_warmed'].astype(int) == 1
        valid_mask = exec_mask & warm_mask
        new_label = pd.Series(0, index=out.index, dtype=int)
        if valid_mask.any():
            new_label.loc[valid_mask] = (mfe.loc[valid_mask] - LABEL_MAE_PENALTY * mae.loc[valid_mask] > LABEL_THRESHOLD).astype(int)
        out['label_class'] = new_label
    if 'signal_type' not in out.columns:
        out['signal_type'] = 'unknown'
    out['signal_type'] = out['signal_type'].astype(str).replace({'': 'unknown'}).fillna('unknown')
    return out.sort_values('datetime').reset_index(drop=True)

def _is_numeric_like_column(series: pd.Series) -> bool:
    """判断列是否能安全作为数值特征（数值/布尔/可被强转的数值字符串）。

    跳过 object dtype 中含真实字符串（如 regime_label）的列，避免被强转为 NaN。
    """
    if pd.api.types.is_bool_dtype(series):
        return True
    if pd.api.types.is_numeric_dtype(series):
        return True
    if pd.api.types.is_object_dtype(series):
        sample = series.dropna()
        if sample.empty:
            return False
        return all((isinstance(v, (int, float, bool, np.integer, np.floating, np.bool_)) for v in sample.head(200)))
    return False

def _select_feature_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    raw_cols = [c for c in out.columns if c.startswith('feature_') or c.startswith('generic_')]
    feature_cols = [c for c in raw_cols if _is_numeric_like_column(out[c])]
    skipped = sorted(set(raw_cols) - set(feature_cols))
    if skipped:
        logger.debug('skip non-numeric feature columns: %s', skipped)
    if not feature_cols:
        if 'side' in out.columns:
            side_series = out['side']
        else:
            side_series = pd.Series([''] * len(out), index=out.index)
        out['feature_side_code'] = np.where(side_series.astype(str).str.lower() == 'long', 1.0, -1.0)
        if 'signal_type' in out.columns:
            st_series = out['signal_type']
        else:
            st_series = pd.Series(['unknown'] * len(out), index=out.index)
        out['feature_signal_code'] = pd.Categorical(st_series.astype(str)).codes.astype(float)
        if 'trigger' in out.columns:
            out['feature_trigger'] = pd.to_numeric(out['trigger'], errors='coerce')
        else:
            out['feature_trigger'] = np.nan
        feature_cols = ['feature_side_code', 'feature_signal_code', 'feature_trigger']
    for c in feature_cols:
        if pd.api.types.is_bool_dtype(out[c]):
            out[c] = out[c].astype(float)
        else:
            out[c] = pd.to_numeric(out[c], errors='coerce')
    feature_cols = [c for c in feature_cols if out[c].notna().any()]
    if not feature_cols:
        out['feature_fallback'] = 0.0
        feature_cols = ['feature_fallback']
    return (out, feature_cols)

def _ensure_binary_label_diversity(df: pd.DataFrame) -> pd.DataFrame:
    """Try to keep label_class with at least two classes within an executed subset.

    重算时**只**对 is_executed==1 的成交样本根据 mfe/mae 重新打 label，
    其它行强制保持 label_class=0，避免把 not_triggered/filtered 的样本
    错误识别为正样本污染 trade-filter 训练。
    """
    out = df.copy()
    if 'label_class' not in out.columns:
        out['label_class'] = 0
    y = pd.to_numeric(out['label_class'], errors='coerce').fillna(0).astype(int)
    if y.nunique() >= 2:
        out['label_class'] = y
        return out
    mfe = pd.to_numeric(out.get('future_mfe_atr', 0.0), errors='coerce').fillna(0.0)
    mae = pd.to_numeric(out.get('future_mae_atr', 0.0), errors='coerce').fillna(0.0)
    if 'is_executed' in out.columns:
        exec_mask = pd.to_numeric(out['is_executed'], errors='coerce').fillna(0).astype(int) == 1
    else:
        exec_mask = pd.Series(True, index=out.index)
    if 'atr_warmed' in out.columns:
        warm_mask = pd.to_numeric(out['atr_warmed'], errors='coerce').fillna(1).astype(int) == 1
    else:
        warm_mask = pd.Series(True, index=out.index)
    valid_mask = exec_mask & warm_mask
    new_label = pd.Series(0, index=out.index, dtype=int)
    if valid_mask.any():
        new_label.loc[valid_mask] = (mfe.loc[valid_mask] - LABEL_MAE_PENALTY * mae.loc[valid_mask] > LABEL_THRESHOLD).astype(int)
    out['label_class'] = new_label
    return out

def _time_split(df: pd.DataFrame, train_end: str, valid_end: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    def _purge_by_exit_datetime(part: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
        if part.empty or 'exit_datetime' not in part.columns:
            return part
        ex = pd.to_datetime(part['exit_datetime'], errors='coerce')
        keep = ex.notna() & (ex <= cutoff)
        return part.loc[keep].copy()
    dt = pd.to_datetime(df['datetime'], errors='coerce')
    train_cut = pd.Timestamp(train_end)
    valid_cut = pd.Timestamp(valid_end)
    train = df.loc[dt <= train_cut].copy()
    valid = df.loc[(dt > train_cut) & (dt <= valid_cut)].copy()
    test = df.loc[dt > valid_cut].copy()
    train = _purge_by_exit_datetime(train, train_cut)
    valid = _purge_by_exit_datetime(valid, valid_cut)
    if train.empty or valid.empty or test.empty:
        n = len(df)
        if n < 6:
            return (df.copy(), df.copy(), df.copy())
        i1 = max(1, int(n * 0.6))
        i2 = max(i1 + 1, int(n * 0.8))
        train = df.iloc[:i1].copy()
        valid = df.iloc[i1:i2].copy()
        test = df.iloc[i2:].copy()
        if test.empty:
            test = df.iloc[-max(1, n // 5):].copy()
        if valid.empty:
            valid = train.copy()
        train_cut_fb = pd.to_datetime(train['datetime'], errors='coerce').max()
        valid_cut_fb = pd.to_datetime(valid['datetime'], errors='coerce').max()
        if pd.notna(train_cut_fb):
            train = _purge_by_exit_datetime(train, pd.Timestamp(train_cut_fb))
        if pd.notna(valid_cut_fb):
            valid = _purge_by_exit_datetime(valid, pd.Timestamp(valid_cut_fb))
    return (train, valid, test)

def _build_walk_forward_windows(df: pd.DataFrame, train_end: str, valid_end: str, max_windows: int, window_mode: WindowMode='expanding', rolling_train_years: int=3, rolling_valid_years: int=1, rolling_test_years: int=1, rolling_step_years: int=1) -> list[_WalkForwardWindow]:
    """Build walk-forward windows.

    window_mode:
        - "expanding": train 起点固定为最早样本，每个窗口 train_end 单调右移；
        - "sliding"  : train 长度固定（首窗口 train 长度），整体滑动；
        - "rolling"  : 固定 train/valid/test 年份（默认 3y/1y/1y），按 step 年滚动。
    任意窗口若 train/valid/test 任一段为空则跳过；当数据已超过 valid_end 也提前退出。
    """
    mode = str(window_mode).lower()
    if mode not in {'expanding', 'sliding', 'rolling'}:
        raise ValueError(f'invalid window_mode={window_mode}, valid=expanding|sliding|rolling')
    dt = pd.to_datetime(df['datetime'], errors='coerce')
    base_train_end = pd.Timestamp(train_end)
    base_valid_end = pd.Timestamp(valid_end)
    if mode == 'rolling':
        train_years = max(1, int(rolling_train_years))
        valid_years = max(1, int(rolling_valid_years))
        test_years = max(1, int(rolling_test_years))
        step_years = max(1, int(rolling_step_years))
        step = pd.DateOffset(years=step_years)
    else:
        step = base_valid_end - base_train_end
        if step <= pd.Timedelta(0):
            step = pd.Timedelta(days=180)
    dt_min = dt.min() if not dt.empty else pd.NaT
    dt_max = dt.max() if not dt.empty else pd.NaT
    if mode == 'rolling':
        base_train_start = base_train_end - pd.DateOffset(years=max(1, int(rolling_train_years)))
    else:
        base_train_start = dt_min if pd.notna(dt_min) else base_train_end - step
    windows: list[_WalkForwardWindow] = []
    n_windows = max(1, int(max_windows))
    for wid in range(n_windows):
        if mode == 'rolling':
            shift = pd.DateOffset(years=wid * step_years)
            w_train_end = base_train_end + shift
            w_train_start = w_train_end - pd.DateOffset(years=train_years)
            w_valid_end = w_train_end + pd.DateOffset(years=valid_years)
            w_test_end = w_valid_end + pd.DateOffset(years=test_years)
            train_mask = (dt > w_train_start) & (dt <= w_train_end)
        elif mode == 'sliding':
            w_train_end = base_train_end + wid * step
            w_valid_end = base_valid_end + wid * step
            w_test_end = w_valid_end + step
            w_train_start = base_train_start + wid * step
            train_mask = (dt > w_train_start) & (dt <= w_train_end) if wid > 0 else dt <= w_train_end
        else:
            w_train_end = base_train_end + wid * step
            w_valid_end = base_valid_end + wid * step
            w_test_end = w_valid_end + step
            w_train_start = base_train_start
            train_mask = dt <= w_train_end
        train_df = df.loc[train_mask].copy()
        valid_df = df.loc[(dt > w_train_end) & (dt <= w_valid_end)].copy()
        test_df = df.loc[(dt > w_valid_end) & (dt <= w_test_end)].copy()
        if 'exit_datetime' in df.columns:
            train_exit = pd.to_datetime(train_df['exit_datetime'], errors='coerce')
            valid_exit = pd.to_datetime(valid_df['exit_datetime'], errors='coerce')
            test_exit = pd.to_datetime(test_df['exit_datetime'], errors='coerce')
            train_df = train_df.loc[train_exit.notna() & (train_exit <= w_train_end)].copy()
            valid_df = valid_df.loc[valid_exit.notna() & (valid_exit <= w_valid_end)].copy()
            test_df = test_df.loc[test_exit.notna() & (test_exit <= w_test_end)].copy()
        if train_df.empty or valid_df.empty or test_df.empty:
            if pd.notna(dt_max) and dt_max <= w_valid_end:
                break
            continue
        windows.append(_WalkForwardWindow(window_id=len(windows), train=train_df, valid=valid_df, test=test_df, train_end=w_train_end, valid_end=w_valid_end, test_end=w_test_end))
        if pd.notna(dt_max) and dt_max <= w_test_end:
            break
    if windows:
        return windows
    train_df, valid_df, test_df = _time_split(df, train_end=train_end, valid_end=valid_end)
    return [_WalkForwardWindow(window_id=0, train=train_df, valid=valid_df, test=test_df, train_end=pd.to_datetime(train_df['datetime'], errors='coerce').max(), valid_end=pd.to_datetime(valid_df['datetime'], errors='coerce').max(), test_end=pd.to_datetime(test_df['datetime'], errors='coerce').max())]


def _build_pool_cross_sectional_candidate_table(
    pool_symbols: Sequence[tuple[str, str | None]],
    *,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str,
    rotation_cfg: CrossSectionalRotationConfig,
) -> pd.DataFrame:
    """Build day-level cross-sectional rotation candidates for pool training.

    Caller must provide an opt-in ``rotation_cfg``. When
    ``use_cross_sectional_momentum_rotation`` is False (default), the function
    returns an empty DataFrame and rotation samples are NOT injected into
    training. 这保证设计文档 §13 "默认 off → 训练样本分布不变" 不变量成立。
    """
    if not bool(getattr(rotation_cfg, "use_cross_sectional_momentum_rotation", False)):
        return pd.DataFrame()
    interval_norm = normalize_interval(interval)
    # 快速路径：cfg 未对该 interval 启用任何 cluster 时直接 return（避免无谓 IO）。
    _PROBE_CLUSTERS = ("black", "metal", "chemical", "agri", "precious", "index", "bond", "other")
    if not any(rotation_cfg.is_enabled(c, interval_norm) for c in _PROBE_CLUSTERS):
        return pd.DataFrame()
    if len(pool_symbols) < 2:
        return pd.DataFrame()

    mode = str(trade_side_mode).strip().lower()
    if mode not in {"both", "long", "short"}:
        mode = "both"

    frame_map: dict[str, pd.DataFrame] = {}
    exchange_map: dict[str, str] = {}
    dt_map: dict[str, np.ndarray] = {}
    bcfg = BacktestConfig(interval=interval_norm)
    for sym_raw, ex_raw in pool_symbols:
        sym = str(sym_raw).upper()
        ex = str(ex_raw).upper() if ex_raw else resolve_exchange(sym, bcfg.symbols_list_path)
        try:
            bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
            frame = prepare_master_feature_frame(bars, interval=interval_norm)
        except Exception:
            logger.exception("cross-sectional: failed to load frame for symbol=%s, skip", sym)
            continue
        if frame.empty or "datetime" not in frame.columns:
            continue
        frame = frame.sort_values("datetime").reset_index(drop=True)
        dt_series = pd.to_datetime(frame["datetime"], errors="coerce")
        if dt_series.isna().all():
            continue
        frame_map[sym] = frame
        exchange_map[sym] = ex
        dt_map[sym] = dt_series.to_numpy(dtype="datetime64[ns]")
    if len(frame_map) < 2:
        return pd.DataFrame()

    all_dates = sorted(
        {
            pd.Timestamp(v).normalize()
            for dt_values in dt_map.values()
            for v in dt_values
            if not pd.isna(v)
        }
    )
    if not all_dates:
        return pd.DataFrame()

    rotation = CrossSectionalMomentumRotation(rotation_cfg)
    last_rebalance_dt: pd.Timestamp | None = None
    rows: list[pd.DataFrame] = []
    for date in all_dates:
        if not _is_rebalance_day(
            date,
            rebalance_weekday=rotation_cfg.rebalance_weekday,
            last_rebalance_dt=last_rebalance_dt,
            max_holding_days=rotation_cfg.max_holding_days,
        ):
            continue
        last_rebalance_dt = pd.Timestamp(date)
        universe_as_of: dict[str, pd.DataFrame] = {}
        for sym, frame in frame_map.items():
            dt_values = dt_map[sym]
            pos = int(dt_values.searchsorted(np.datetime64(date), side="right"))
            if pos <= 0:
                continue
            universe_as_of[sym] = frame.iloc[:pos]
        if len(universe_as_of) < 2:
            continue
        cand = rotation.generate_rebalance_candidates(
            pd.Timestamp(date),
            universe_as_of,
            interval=interval_norm,
            last_rebalance_dt=last_rebalance_dt,
            current_drawdown_pct=0.0,
        )
        if cand.empty:
            continue
        if mode == "short":
            cand = cand.loc[cand["side"].astype(str).str.lower() == "short"].copy()
        elif mode == "long":
            cand = cand.loc[cand["side"].astype(str).str.lower() == "long"].copy()
        if cand.empty:
            continue
        rows.append(
            _convert_pool_cross_sectional_candidates_to_training_rows(
                cand,
                frame_map=frame_map,
                exchange_map=exchange_map,
                interval=interval_norm,
                stop_loss_pct=float(rotation_cfg.stop_loss_pct),
            )
        )
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, axis=0, ignore_index=True)
    if out.empty:
        return out
    return out.sort_values(["datetime", "symbol", "side"]).reset_index(drop=True)


def _convert_pool_cross_sectional_candidates_to_training_rows(
    candidates: pd.DataFrame,
    *,
    frame_map: dict[str, pd.DataFrame],
    exchange_map: dict[str, str],
    interval: str,
    stop_loss_pct: float,
) -> pd.DataFrame:
    """Convert rotation rebalance candidates to canonical candidate rows."""
    out_rows: list[dict[str, object]] = []
    for _, c in candidates.iterrows():
        sym = str(c.get("symbol", "")).upper()
        side = str(c.get("side", "")).lower()
        if not sym or side not in {"long", "short"}:
            continue
        frame = frame_map.get(sym)
        ex = exchange_map.get(sym, "")
        if frame is None or frame.empty:
            continue
        dt = pd.to_datetime(frame["datetime"], errors="coerce")
        if dt.isna().all():
            continue
        signal_dt = pd.Timestamp(c.get("signal_datetime"))
        entry_dt = pd.Timestamp(c.get("entry_datetime"))
        planned_exit_dt = pd.Timestamp(c.get("planned_exit_datetime"))
        signal_i = int(dt.searchsorted(signal_dt, side="right") - 1)
        entry_i = int(dt.searchsorted(entry_dt, side="left"))
        if signal_i < 0 or entry_i <= signal_i or entry_i >= len(frame):
            continue
        horizon_i = int(dt.searchsorted(planned_exit_dt, side="right") - 1)
        horizon_i = min(max(entry_i + 1, horizon_i), len(frame) - 1)
        if horizon_i <= entry_i:
            continue

        signal_bar = frame.iloc[signal_i]
        entry_bar = frame.iloc[entry_i]
        atr = pd.to_numeric(pd.Series([entry_bar.get("atr14", np.nan)]), errors="coerce").iloc[0]
        if not np.isfinite(atr) or atr <= 0:
            hi = pd.to_numeric(pd.Series([entry_bar.get("high", np.nan)]), errors="coerce").iloc[0]
            lo = pd.to_numeric(pd.Series([entry_bar.get("low", np.nan)]), errors="coerce").iloc[0]
            atr = float(hi - lo) if np.isfinite(hi) and np.isfinite(lo) and hi > lo else np.nan
        atr_warmed = int(np.isfinite(atr) and atr > 0)

        entry_open = pd.to_numeric(pd.Series([entry_bar.get("open", np.nan)]), errors="coerce").iloc[0]
        entry_close = pd.to_numeric(pd.Series([entry_bar.get("close", np.nan)]), errors="coerce").iloc[0]
        entry_price = float(entry_open if np.isfinite(entry_open) else entry_close)
        if not np.isfinite(entry_price):
            continue
        sim = _simulate_candidate_execution_path(
            frame,
            entry_i=entry_i,
            horizon_i=horizon_i,
            side=side,
            entry_price=entry_price,
            atr_v=float(atr if np.isfinite(atr) else 1.0),
            stop_loss_pct=float(stop_loss_pct),
        )
        mfe_atr = pd.to_numeric(pd.Series([sim.get("mfe_atr", np.nan)]), errors="coerce").iloc[0]
        mae_atr = pd.to_numeric(pd.Series([sim.get("mae_atr", np.nan)]), errors="coerce").iloc[0]
        pnl_atr = pd.to_numeric(pd.Series([sim.get("pnl_atr", np.nan)]), errors="coerce").iloc[0]
        exit_price = pd.to_numeric(pd.Series([sim.get("exit_price", np.nan)]), errors="coerce").iloc[0]
        if atr_warmed and np.isfinite(mfe_atr) and np.isfinite(mae_atr):
            edge = float(mfe_atr) - LABEL_MAE_PENALTY * float(mae_atr)
            label_class = int(edge > LABEL_THRESHOLD)
        elif np.isfinite(pnl_atr):
            label_class = int(float(pnl_atr) > LABEL_THRESHOLD)
        else:
            label_class = 0

        stop_price = pd.to_numeric(pd.Series([c.get("stop_price", np.nan)]), errors="coerce").iloc[0]
        if not np.isfinite(stop_price):
            if side == "long":
                stop_price = entry_price * (1.0 - float(stop_loss_pct))
            else:
                stop_price = entry_price * (1.0 + float(stop_loss_pct))

        row: dict[str, object] = {
            "symbol": sym,
            "exchange": ex,
            "interval": interval,
            "datetime": dt.iloc[entry_i],
            "signal_datetime": dt.iloc[signal_i],
            "exit_datetime": dt.iloc[horizon_i],
            "signal_type": CROSS_SECTIONAL_SIGNAL_TYPE,
            "side": side,
            "order_type": "market",
            "signal_i": signal_i,
            "entry_i": entry_i,
            "horizon_i": horizon_i,
            "is_horizon_truncated": 0,
            "entry_price": float(entry_price),
            "exit_price_ref": float(exit_price) if np.isfinite(exit_price) else np.nan,
            "stop_price": float(stop_price) if np.isfinite(stop_price) else np.nan,
            "target_price": np.nan,
            "trigger": float(entry_price),
            "future_mfe_atr": float(mfe_atr) if np.isfinite(mfe_atr) else np.nan,
            "future_mae_atr": float(mae_atr) if np.isfinite(mae_atr) else np.nan,
            "future_pnl_atr": float(pnl_atr) if np.isfinite(pnl_atr) else np.nan,
            "atr_warmed": atr_warmed,
            "label_class": int(label_class),
            "regime_label": _infer_regime_label(signal_bar),
            "candidate_status": "filled",
            "is_executed": 1,
            "is_filtered": 0,
            "is_triggered": 1,
            "filtered_reason": "",
            "feature_rotation_momentum_score": pd.to_numeric(pd.Series([c.get("momentum_score", np.nan)]), errors="coerce").iloc[0],
            "feature_rotation_momentum_rank": pd.to_numeric(pd.Series([c.get("momentum_rank", np.nan)]), errors="coerce").iloc[0],
            "feature_rotation_percentile_cluster": pd.to_numeric(pd.Series([c.get("percentile_in_cluster", np.nan)]), errors="coerce").iloc[0],
            "feature_rotation_percentile_universe": pd.to_numeric(pd.Series([c.get("percentile_in_universe", np.nan)]), errors="coerce").iloc[0],
            "feature_rotation_vol_target_scale": pd.to_numeric(pd.Series([c.get("vol_target_scale", np.nan)]), errors="coerce").iloc[0],
            "feature_rotation_target_weight": pd.to_numeric(pd.Series([c.get("target_weight", np.nan)]), errors="coerce").iloc[0],
        }
        for col in TRAINING_FEATURE_COLUMNS:
            row[f"feature_{col}"] = signal_bar[col] if col in frame.columns else np.nan
        out_rows.append(row)
    return pd.DataFrame(out_rows)

def _build_pooled_feature_df(pool_symbols: Sequence[tuple[str, str | None]], *, interval: str, start_date: str, end_date: str, trade_side_mode: str, synthetic_periods: int, feature_root: Path, generic_columns: Any, rotation_cfg: CrossSectionalRotationConfig | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Thin wrapper for pooled table builder (moved to pipeline_pooling module).

    rotation_cfg: 默认 None → 不向训练样本注入 cross_sectional_momentum 候选，
    符合设计文档 §13 "默认 off" 不变量。调用方需显式构造启用的 cfg 才会触发。
    """
    pooled_cand, pooled_feat = _build_pooled_feature_df_impl(
        pool_symbols,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        trade_side_mode=trade_side_mode,
        synthetic_periods=synthetic_periods,
        feature_root=feature_root,
        generic_columns=generic_columns,
        build_candidate_table_fn=_build_candidate_table,
        ensure_training_columns_fn=_ensure_training_columns,
        build_training_feature_table_with_auto_fallback_fn=_build_training_feature_table_with_auto_fallback,
    )
    interval_norm = normalize_interval(interval)
    if rotation_cfg is None or not bool(rotation_cfg.use_cross_sectional_momentum_rotation):
        return pooled_cand, pooled_feat
    # cfg 控制 interval 启用集合；day 之外的 interval 由 cfg.is_enabled / pool_symbols
    # 数量自然过滤（非 day 通常 pool_symbols < 2 → 直接 return empty）。
    xsec_cand = _build_pool_cross_sectional_candidate_table(
        pool_symbols,
        interval=interval_norm,
        start_date=start_date,
        end_date=end_date,
        trade_side_mode=trade_side_mode,
        rotation_cfg=rotation_cfg,
    )
    if xsec_cand.empty:
        return pooled_cand, pooled_feat
    xsec_cand = _ensure_training_columns(xsec_cand)
    xsec_feat_parts: list[pd.DataFrame] = []
    for sym, group in xsec_cand.groupby("symbol"):
        sym_key = str(sym).upper()
        try:
            f = _build_training_feature_table_with_auto_fallback(
                candidate_df=group.copy(),
                symbol=sym_key,
                interval=interval_norm,
                feature_root=feature_root,
                generic_columns=generic_columns,
            )
            f = _ensure_training_columns(f)
            f["symbol"] = sym_key
            xsec_feat_parts.append(f)
        except Exception:
            logger.exception("cross-sectional: feature merge failed for symbol=%s; skipping xsec rows", sym_key)
    if not xsec_feat_parts:
        return pooled_cand, pooled_feat
    xsec_feat = pd.concat(xsec_feat_parts, axis=0, ignore_index=True)
    pooled_cand = pd.concat([pooled_cand, xsec_cand], axis=0, ignore_index=True)
    pooled_feat = pd.concat([pooled_feat, xsec_feat], axis=0, ignore_index=True)
    if "datetime" in pooled_cand.columns:
        pooled_cand = pooled_cand.sort_values("datetime").reset_index(drop=True)
    if "datetime" in pooled_feat.columns:
        pooled_feat = pooled_feat.sort_values("datetime").reset_index(drop=True)
    logger.info(
        "cross-sectional candidates merged into day pool: added_candidates=%d added_features=%d",
        len(xsec_cand),
        len(xsec_feat),
    )
    return pooled_cand, pooled_feat

def _build_candidate_table(symbol: str, exchange: str | None, interval: str, start_date: str, end_date: str, trade_side_mode: str, synthetic_periods: int, allow_synthetic_fallback: bool=True) -> tuple[pd.DataFrame, str]:
    sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    bcfg = BacktestConfig(interval=interval_norm)
    ex = str(exchange).upper() if exchange else resolve_exchange(sym, bcfg.symbols_list_path)
    try:
        bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
        frame = prepare_master_feature_frame(bars, interval=interval_norm)
        parts: list[pd.DataFrame] = []
        for st in BASELINE_SIGNAL_TYPES:
            cand = generate_candidate_opportunities(frame=frame, symbol=sym, exchange=ex, interval=interval_norm, signal_type=st, horizon_bars=20, trade_side_mode=trade_side_mode)
            if not cand.empty:
                parts.append(cand)
        if parts:
            out = pd.concat(parts, axis=0, ignore_index=True).sort_values('datetime').reset_index(drop=True)
            return (out, ex)
        if not allow_synthetic_fallback:
            raise ValueError(f'candidate table empty from real data for {sym}.{ex} {interval_norm}, synthetic fallback disabled')
        logger.warning('candidate table empty from real data, switching to synthetic fallback')
    except Exception as exc:
        if not allow_synthetic_fallback:
            raise
        logger.warning('candidate generation from local data failed, fallback to synthetic: %s', exc)
    synth = _build_synthetic_candidate(symbol=sym, exchange=ex, interval=interval_norm, start_date=start_date, end_date=end_date, periods=synthetic_periods)
    return (synth, ex)

def _resolve_generic_columns(generic_mode: str) -> Iterable[str] | None:
    """Translate ``generic_mode`` to the ``generic_columns`` arg of merge helpers.

    - ``"auto"`` (默认): None → 由 merge_candidate_and_generic_features 自动取
      磁盘 parquet 上所有数值列（排除 OHLCV / 元数据），把 cta/data/feature 里
      预先算好的 ~400 个特征全部带进训练。
    - ``"whitelist"``: 锁定 18 列窄白名单 ``DEFAULT_GENERIC_COLUMNS``，复现
      历史行为或减少特征维度。
    """
    mode = str(generic_mode).strip().lower()
    if mode == 'auto':
        return None
    if mode == 'whitelist':
        return DEFAULT_GENERIC_COLUMNS
    raise ValueError(f"invalid generic_mode={generic_mode!r}, expected 'auto' or 'whitelist'")
