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

def _build_pooled_feature_df(pool_symbols: Sequence[tuple[str, str | None]], *, interval: str, start_date: str, end_date: str, trade_side_mode: str, synthetic_periods: int, feature_root: Path, generic_columns: Any) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Thin wrapper for pooled table builder (moved to pipeline_pooling module)."""
    return _build_pooled_feature_df_impl(pool_symbols, interval=interval, start_date=start_date, end_date=end_date, trade_side_mode=trade_side_mode, synthetic_periods=synthetic_periods, feature_root=feature_root, generic_columns=generic_columns, build_candidate_table_fn=_build_candidate_table, ensure_training_columns_fn=_ensure_training_columns, build_training_feature_table_with_auto_fallback_fn=_build_training_feature_table_with_auto_fallback)

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
