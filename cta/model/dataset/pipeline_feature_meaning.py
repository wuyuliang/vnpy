"""Feature meaning helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import (
    FEATURES_DOC_PATH,
    Path,
    _FEATURE_MEANING_FALLBACK,
    lru_cache,
    re,
)

@lru_cache(maxsize=8)
def _load_feature_meaning_map_cached(features_doc_path: str, mtime_ns: int) -> dict[str, str]:
    """Inner cached loader keyed by (path, mtime_ns).

    D4 fix：单跟 path 缓存会让长跑进程在 FEATURES.md 改动后仍取老映射。把 mtime
    放进缓存键，文件改动后自动失效一次重读。``mtime_ns`` 是不存在文件的兜底 0。
    """
    path = Path(features_doc_path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding='utf-8-sig')
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    pat = re.compile('^\\|\\s*`([^`]+)`\\s*\\|\\s*([^|]+?)\\s*\\|')
    for raw in text.splitlines():
        line = raw.strip()
        m = pat.match(line)
        if not m:
            continue
        key = str(m.group(1)).strip()
        meaning = str(m.group(2)).strip()
        if key and meaning and (key not in mapping):
            mapping[key] = meaning
    return mapping

def _load_feature_meaning_map(features_doc_path: str) -> dict[str, str]:
    """Load ``feature_name -> meaning`` mapping from FEATURES.md markdown tables.

    Public wrapper that derives the cache key (path + mtime) so callers stay
    backward-compatible with the old single-arg signature.
    """
    p = Path(features_doc_path)
    try:
        mtime_ns = p.stat().st_mtime_ns if p.exists() else 0
    except OSError:
        mtime_ns = 0
    return _load_feature_meaning_map_cached(features_doc_path, mtime_ns)

def _feature_meaning(feature_name: str, features_doc_path: Path=FEATURES_DOC_PATH) -> str:
    key = str(feature_name).strip()
    if not key:
        return ''
    if key in _FEATURE_MEANING_FALLBACK:
        return _FEATURE_MEANING_FALLBACK[key]
    mapping = _load_feature_meaning_map(str(features_doc_path.resolve()))
    candidates = [key]
    if key.startswith('generic_'):
        candidates.append(key[len('generic_'):])
    if key.startswith('feature_'):
        candidates.append(key[len('feature_'):])
    for c in candidates:
        if c in mapping:
            return mapping[c]
    return '未在 FEATURES.md 登记（自定义/衍生特征）'
