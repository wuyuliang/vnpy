"""Diagnostics helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import infer_symbol_cluster, logger, np, pd
from cta.model.dataset.pipeline_feature_meaning import _feature_meaning

def _build_symbol_cluster_sample_weight(df: pd.DataFrame) -> pd.Series:
    """Build per-row weights to compensate dense symbol clusters in pooled training.

    P1.2 双层加权：
    1) cluster 维度：sqrt(n_clusters_in_use / symbols_in_this_cluster) —
       样本品种多的板块（如黑色 5 个 vs 贵金属 2 个）权重小化，使各板块边际样本权重均衡。
    2) cluster 内 symbol 维度：sqrt(median_samples_in_cluster / this_symbol_samples) —
       同板块内样本悬殊（RB 1万 vs HC 500）时给小样本品种更高权重。
    最终 weight = w_cluster × w_within_cluster。
    """
    if df.empty or 'symbol' not in df.columns:
        return pd.Series(np.ones(len(df), dtype=float), index=df.index, dtype=float)
    sym = df['symbol'].astype(str).str.upper().fillna('')
    unique_symbols = sorted({s for s in sym.tolist() if s})
    if len(unique_symbols) <= 1:
        return pd.Series(np.ones(len(df), dtype=float), index=df.index, dtype=float)
    cluster_by_symbol = {s: infer_symbol_cluster(s) for s in unique_symbols}
    cluster_symbol_count: dict[str, int] = {}
    for s in unique_symbols:
        cl = cluster_by_symbol.get(s, 'other')
        cluster_symbol_count[cl] = int(cluster_symbol_count.get(cl, 0) + 1)
    sym_sample_count = sym.value_counts().to_dict()
    cluster_sym_samples: dict[str, list[int]] = {}
    for s, n in sym_sample_count.items():
        cl = cluster_by_symbol.get(str(s), 'other')
        cluster_sym_samples.setdefault(cl, []).append(int(n))
    cluster_median_samples = {cl: float(np.median(arr)) if arr else 1.0 for cl, arr in cluster_sym_samples.items()}
    n_clusters_in_use = float(len(cluster_symbol_count))
    w = pd.Series(np.ones(len(df), dtype=float), index=df.index, dtype=float)
    for idx, s in sym.items():
        s_str = str(s)
        cl = cluster_by_symbol.get(s_str, 'other')
        cnt = max(1, int(cluster_symbol_count.get(cl, 1)))
        w_cluster = float(np.sqrt(n_clusters_in_use / float(cnt)))
        median_in_cluster = max(1.0, float(cluster_median_samples.get(cl, 1.0)))
        this_sym_samples = max(1.0, float(sym_sample_count.get(s_str, 1)))
        w_within = float(np.sqrt(median_in_cluster / this_sym_samples))
        w.at[idx] = float(w_cluster * w_within)
    return w

def _empty_top_feature_importance_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=['signal_type', 'window_id', 'model', 'model_kind', 'rank', 'feature', 'importance', 'feature_meaning'])

def _build_valid_test_gap_alerts(metrics_df: pd.DataFrame, *, max_gap: float) -> pd.DataFrame:
    """把每个 (signal_type, window_id, model) 的 valid/test AUC 差 > max_gap 的行抓出来。

    test 不参与选参，但若 valid AUC 与 test AUC 差距过大，说明 valid 与 test 分布偏离
    或存在仅在 valid 期可见的弱信号 — 是潜在过拟合 / 时间漂移的提示。
    """
    cols = ['signal_type', 'window_id', 'model', 'split', 'auc']
    if metrics_df is None or metrics_df.empty or (not set(cols).issubset(metrics_df.columns)):
        return pd.DataFrame(columns=['signal_type', 'window_id', 'model', 'valid_auc', 'test_auc', 'gap'])
    sub = metrics_df.loc[metrics_df['split'].isin(['valid', 'test']), cols].copy()
    if sub.empty:
        return pd.DataFrame(columns=['signal_type', 'window_id', 'model', 'valid_auc', 'test_auc', 'gap'])
    sub['auc'] = pd.to_numeric(sub['auc'], errors='coerce')
    pivot = sub.pivot_table(index=['signal_type', 'window_id', 'model'], columns='split', values='auc', aggfunc='last').reset_index()
    pivot.columns.name = None
    if 'valid' not in pivot.columns:
        pivot['valid'] = float('nan')
    if 'test' not in pivot.columns:
        pivot['test'] = float('nan')
    pivot = pivot.rename(columns={'valid': 'valid_auc', 'test': 'test_auc'})
    pivot['gap'] = (pd.to_numeric(pivot['valid_auc'], errors='coerce') - pd.to_numeric(pivot['test_auc'], errors='coerce')).abs()
    out = pivot.loc[pivot['gap'].fillna(0.0) > float(max_gap)].copy()
    out = out.sort_values('gap', ascending=False).reset_index(drop=True)
    return out[['signal_type', 'window_id', 'model', 'valid_auc', 'test_auc', 'gap']]

def _build_top_feature_concentration_alerts(top_feature_df: pd.DataFrame, *, top1_thresh: float) -> pd.DataFrame:
    """top-1 特征在 top-10 集合内占比 > 阈值时告警。

    输入 ``top_feature_df`` 是 ``_tag_top_feature_importance`` 的产物，**每个
    (signal_type, window_id, model) 只保留 top-10 特征**。本函数计算的指标是：
    ``top1_pct_in_top10 = importance(top1) / sum(importance(top1..top10))``。

    这是一个**相对集中度**指标，不等同于"top1 在全集 ~400 个特征里的真实占比"。
    在 top-10 内占比超过 ``top1_thresh``（默认 0.5）说明 top-10 的"次重要特征贡献微弱"，
    高度依赖单一特征，常见于穿越或常数特征。要看真实全集占比请检查
    *_feature_manifest.csv（dump_feature_manifest 落盘的全集 importance）。
    """
    cols = ['signal_type', 'window_id', 'model', 'feature', 'importance']
    out_cols = ['signal_type', 'window_id', 'model', 'top_feature', 'top1_pct_in_top10']
    if top_feature_df is None or top_feature_df.empty or (not set(cols).issubset(top_feature_df.columns)):
        return pd.DataFrame(columns=out_cols)
    df = top_feature_df.loc[:, cols].copy()
    df['importance'] = pd.to_numeric(df['importance'], errors='coerce').fillna(0.0)
    rows: list[dict[str, Any]] = []
    for (sig, win, mdl), grp in df.groupby(['signal_type', 'window_id', 'model'], sort=False):
        total = float(grp['importance'].sum())
        if total <= 0:
            continue
        top_row = grp.sort_values('importance', ascending=False).iloc[0]
        pct = float(top_row['importance']) / total
        if pct >= float(top1_thresh):
            rows.append({'signal_type': str(sig), 'window_id': int(win), 'model': str(mdl), 'top_feature': str(top_row['feature']), 'top1_pct_in_top10': float(pct)})
    if not rows:
        return pd.DataFrame(columns=out_cols)
    out = pd.DataFrame(rows).sort_values('top1_pct_in_top10', ascending=False).reset_index(drop=True)
    return out

def _tag_top_feature_importance(top_df: pd.DataFrame, *, signal_type: str, window_id: int, model: str, model_kind: str) -> pd.DataFrame:
    out = top_df.copy()
    if out.empty:
        return _empty_top_feature_importance_frame()
    if 'feature' not in out.columns or 'importance' not in out.columns:
        return _empty_top_feature_importance_frame()
    out = out[['feature', 'importance']].copy()
    out['importance'] = pd.to_numeric(out['importance'], errors='coerce').fillna(0.0)
    out = out.sort_values(['importance', 'feature'], ascending=[False, True]).reset_index(drop=True)
    out['rank'] = np.arange(1, len(out) + 1, dtype=int)
    out['feature_meaning'] = out['feature'].astype(str).map(_feature_meaning)
    out['signal_type'] = str(signal_type)
    out['window_id'] = int(window_id)
    out['model'] = str(model)
    out['model_kind'] = str(model_kind)
    return out[['signal_type', 'window_id', 'model', 'model_kind', 'rank', 'feature', 'importance', 'feature_meaning']]

def _dump_feature_manifest(*, joblib_path: Path, feature_columns: Sequence[str], importance_df: pd.DataFrame | None, model_kind: str) -> Path | None:
    """Persist a per-model feature manifest beside the joblib model.

    部署时下游需要严格按"训练用过的特征清单"做 schema 校验。每个
    ``<model>.joblib`` 旁边写一份 ``<model>_features.csv``，列：
    ``rank / feature / importance / feature_meaning``，按 importance 降序。

    防御式实现：
    1. 永远写出全部 ``feature_columns``（不只 top-k），哪怕 importance 缺失也把缺失行
       置为 0.0 后排在末尾；下游能拿到完整 schema 是关键。
    2. ``importance_df`` 为 None / 空 / 列缺失时也不抛异常，全部置 0.0。
    3. 写文件用 utf-8-sig + index=False，与 pipeline 其它产物保持一致。
    4. 返回写出的 path 便于日志追踪；joblib 不存在时返回 None（孤儿保护）。
    """
    if not joblib_path.exists():
        logger.warning('skip feature manifest: joblib not found at %s (mfe_mae skipped or save failed)', joblib_path)
        return None
    feats = [str(c) for c in feature_columns if str(c).strip()]
    if not feats:
        logger.warning('skip feature manifest for %s: empty feature_columns', joblib_path)
        return None
    imp_map: dict[str, float] = {f: 0.0 for f in feats}
    try:
        if importance_df is not None and (not importance_df.empty) and {'feature', 'importance'}.issubset(set(importance_df.columns)):
            for _, r in importance_df.iterrows():
                fname = str(r['feature'])
                if fname not in imp_map:
                    continue
                v = pd.to_numeric(r['importance'], errors='coerce')
                imp_map[fname] = float(v) if pd.notna(v) else 0.0
    except Exception as exc:
        logger.warning('feature manifest importance parse failed for %s: %s', joblib_path, exc)
    df = pd.DataFrame({'feature': feats, 'importance': [imp_map[f] for f in feats]})
    df = df.drop_duplicates(subset=['feature'], keep='first')
    df = df.sort_values(['importance', 'feature'], ascending=[False, True]).reset_index(drop=True)
    df['rank'] = np.arange(1, len(df) + 1, dtype=int)
    df['feature_meaning'] = df['feature'].astype(str).map(_feature_meaning)
    df['model_kind'] = str(model_kind)
    df = df[['rank', 'feature', 'importance', 'feature_meaning', 'model_kind']]
    manifest_path = joblib_path.with_name(joblib_path.stem + '_features.csv')
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(manifest_path, index=False, encoding='utf-8-sig')
    except Exception as exc:
        logger.warning('failed writing feature manifest %s: %s', manifest_path, exc)
        return None
    return manifest_path

def _log_top_feature_importance(top_df: pd.DataFrame) -> None:
    if top_df.empty:
        return
    sig = str(top_df['signal_type'].iloc[0])
    wid = int(top_df['window_id'].iloc[0])
    model = str(top_df['model'].iloc[0])
    model_kind = str(top_df['model_kind'].iloc[0])
    rows = [f'{r.feature}={float(r.importance):.6f}' for r in top_df.itertuples(index=False)]
    logger.info('top10 feature importance | signal=%s window=%s model=%s kind=%s | %s', sig, wid, model, model_kind, ', '.join(rows))

def _final_model_importance_df(model: FinalDecisionModel, feature_columns: Sequence[str], *, top_k: int) -> pd.DataFrame:
    feats = [str(c) for c in feature_columns]
    if not feats or int(top_k) <= 0:
        return pd.DataFrame(columns=['feature', 'importance'])
    imp = np.zeros(len(feats), dtype=float)
    est = model.estimator
    if est is not None and hasattr(est, 'named_steps'):
        clf = est.named_steps.get('model')
        if clf is not None and hasattr(clf, 'coef_'):
            coef = np.asarray(getattr(clf, 'coef_'), dtype=float)
            if coef.ndim == 2:
                coef = np.mean(np.abs(coef), axis=0)
            else:
                coef = np.abs(coef)
            imp = np.asarray(coef, dtype=float).reshape(-1)
    if imp.shape[0] != len(feats):
        imp = np.resize(imp, len(feats))
    out = pd.DataFrame({'feature': feats, 'importance': np.nan_to_num(imp, nan=0.0, posinf=0.0, neginf=0.0)})
    out = out.sort_values(['importance', 'feature'], ascending=[False, True]).head(int(top_k)).reset_index(drop=True)
    return out
