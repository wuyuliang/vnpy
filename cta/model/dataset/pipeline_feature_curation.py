"""Feature curation helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import CAUSALITY_MANIFEST_PATH, Path, pd, re

def _load_causality_manifest(manifest_path: Path=CAUSALITY_MANIFEST_PATH) -> dict[str, bool]:
    """Load feature causality manifest (feature -> causal flag)."""
    path = Path(manifest_path)
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, encoding='utf-8-sig')
    except Exception:
        return {}
    if 'feature' not in df.columns or 'causal' not in df.columns:
        return {}
    out: dict[str, bool] = {}
    for _, row in df.iterrows():
        feat = str(row.get('feature', '')).strip()
        if not feat:
            continue
        c = pd.to_numeric(pd.Series([row.get('causal', 1)]), errors='coerce').fillna(1).iloc[0]
        out[feat.lower()] = bool(int(c) != 0)
    return out

def _apply_causality_manifest_filter(feature_columns: Sequence[str], *, manifest_path: Path=CAUSALITY_MANIFEST_PATH) -> list[str]:
    """Drop manifest-marked non-causal features."""
    manifest = _load_causality_manifest(manifest_path=manifest_path)
    if not manifest:
        return [str(c) for c in feature_columns if str(c).strip()]
    out: list[str] = []
    for c in feature_columns:
        name = str(c).strip()
        if not name:
            continue
        lower = name.lower()
        allow = manifest.get(lower, True)
        if not allow:
            continue
        out.append(name)
    return out

def _list_unaudited_features(feature_columns: Sequence[str], *, manifest_path: Path=CAUSALITY_MANIFEST_PATH) -> list[str]:
    """Return features not listed in the causality manifest.

    P1.5：未列名特征**默认通过**（避免误杀大量 generic_*），但生成"待审计"清单，
    让团队增量补全 manifest，最终覆盖所有特征。
    """
    manifest = _load_causality_manifest(manifest_path=manifest_path)
    if not manifest:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for c in feature_columns:
        name = str(c).strip()
        if not name:
            continue
        lower = name.lower()
        if lower in seen:
            continue
        seen.add(lower)
        if lower not in manifest:
            out.append(name)
    return out

def _filter_model_leakage_features(feature_columns: Sequence[str], *, model_name: str) -> list[str]:
    """Filter known leakage-prone features by model type."""
    leak_token_pattern = re.compile('(^|_)(label|future|target|next|lead|forward|fwd|pred|rollingmax|rollingmin|centered|lookahead|peek|aft|shiftneg)($|_)')

    def _core_name(name: str) -> str:
        lower = str(name).strip().lower()
        for prefix in ('feature_', 'generic_'):
            if lower.startswith(prefix):
                return lower[len(prefix):]
        return lower
    seen: set[str] = set()
    out: list[str] = []
    m = str(model_name).strip().lower()
    regime_exact_block = {'feature_trend_score', 'feature_trend_dir', 'generic_auto_trend', 'generic_model_regime_state'}
    regime_proxy_block = {
        'generic_auto_side_code',
        'generic_model_mfe_side_interaction',
        'generic_model_mfe_edge',
        'generic_model_trade_breakout_trend',
    }
    for c in feature_columns:
        name = str(c).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        lower = name.lower()
        core = _core_name(lower)
        if lower.startswith('label_') or lower.startswith('future_') or lower.startswith('pred_'):
            continue
        if lower in {'regime_label', 'label_class'}:
            continue
        if leak_token_pattern.search(core):
            continue
        if m == 'regime_classifier':
            if lower in regime_exact_block:
                continue
            if lower in regime_proxy_block:
                continue
            if lower.startswith('feature_trend_'):
                continue
            if lower.startswith('generic_model_regime_'):
                continue
        out.append(name)
    return out
