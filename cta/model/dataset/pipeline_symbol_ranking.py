"""Symbol ranking helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import Path, _safe_name, infer_symbol_cluster, np, pd

def _resolve_run_exchange(exchange_from_rank: str | None, cli_exchange: str | None) -> str | None:
    """Resolve the runtime exchange given ranking-supplied + CLI inputs.

    D1 fix：旧实现 ``exchange_from_rank or str(args.exchange).upper() if args.exchange else None``
    被 Python 解析成 ``(exchange_from_rank or str(args.exchange).upper()) if args.exchange else None``，
    在 CLI 没传 ``--exchange`` 时即使 ranking 已提供 SHFE 也会被丢成 None。
    抽出来做白盒测试，避免跨模块靠操作符优先级隐式依赖。
    """
    if exchange_from_rank:
        return str(exchange_from_rank).upper()
    if cli_exchange:
        return str(cli_exchange).upper()
    return None

def _load_top_n_symbols_from_ranking(ranking_path: Path, top_n: int, *, respect_disabled_manifest: bool=True) -> list[tuple[str, str | None]]:
    """Load top-N symbols ordered by research_rank from ranking csv.

    M2：加载完成后会调用 ``filter_out_disabled_pairs`` 把 symbol_disable_manifest
    标记的"持续亏损 / 数据源不一致 / 流动性不足"品种剔除，再按 top-N 截断。
    顺序：先按 research_rank 排序 → 再过滤 disabled → 取前 top_n。
    这样剔除 3 个 disabled 后仍能保证最终 top-N 个有效品种参与训练。
    """
    n = int(top_n)
    if n <= 0:
        return []
    path = Path(ranking_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f'symbols ranking csv not found: {path}')
    df = pd.read_csv(path, encoding='utf-8-sig')
    if 'symbol' not in df.columns:
        raise KeyError(f'ranking csv missing symbol column: {path}')
    df = df.copy()
    if 'research_rank' in df.columns:
        df['_rank'] = pd.to_numeric(df['research_rank'], errors='coerce')
    else:
        df['_rank'] = np.arange(len(df), dtype=float)
    df['_rank'] = df['_rank'].fillna(np.inf)
    df = df.sort_values(['_rank']).reset_index(drop=True)
    all_pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        sym = str(row.get('symbol', '')).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ex_raw = row.get('exchange', None)
        ex = str(ex_raw).strip().upper() if pd.notna(ex_raw) and str(ex_raw).strip() else None
        all_pairs.append((sym, ex))
    if respect_disabled_manifest:
        from cta.config.symbol_disable import filter_out_disabled_pairs
        filtered = filter_out_disabled_pairs(all_pairs)
    else:
        filtered = list(all_pairs)
    picked = filtered[:n]
    if not picked:
        raise ValueError(f'no valid symbols loaded from ranking csv: {path}; check symbol_disable_manifest.csv if you expect more')
    return picked

def _load_symbol_groups_from_ranking(ranking_path: Path, *, top_n: int=0, group_by: str='tier', min_symbols_per_group: int=2, respect_disabled_manifest: bool=True) -> list[tuple[str, list[tuple[str, str | None]]]]:
    """Load symbols from ranking csv and split into ordered groups.

    分组来源：
    - ``group_by='cluster'``：基于 ``infer_symbol_cluster``；
    - 其它值：按 ranking csv 同名列（大小写不敏感）分组。
    """
    path = Path(ranking_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f'symbols ranking csv not found: {path}')
    df = pd.read_csv(path, encoding='utf-8-sig')
    if 'symbol' not in df.columns:
        raise KeyError(f'ranking csv missing symbol column: {path}')
    work = df.copy()
    if 'research_rank' in work.columns:
        work['_rank'] = pd.to_numeric(work['research_rank'], errors='coerce')
    else:
        work['_rank'] = np.arange(len(work), dtype=float)
    work['_rank'] = work['_rank'].fillna(np.inf)
    work = work.sort_values(['_rank']).reset_index(drop=True)
    row_by_symbol: dict[str, dict[str, Any]] = {}
    ordered_pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for _, row in work.iterrows():
        sym = str(row.get('symbol', '')).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ex_raw = row.get('exchange', None)
        ex = str(ex_raw).strip().upper() if pd.notna(ex_raw) and str(ex_raw).strip() else None
        ordered_pairs.append((sym, ex))
        row_by_symbol[sym] = dict(row)
    if respect_disabled_manifest:
        from cta.config.symbol_disable import filter_out_disabled_pairs
        ordered_pairs = filter_out_disabled_pairs(ordered_pairs)
    n = int(top_n)
    if n > 0:
        ordered_pairs = ordered_pairs[:n]
    if not ordered_pairs:
        raise ValueError(f'no symbols left after ranking/group filter: {path}')
    group_by_raw = str(group_by).strip()
    group_by_key = _safe_name(group_by_raw or 'tier')
    lower_to_col = {str(c).strip().lower(): str(c) for c in work.columns}
    group_col = lower_to_col.get(group_by_raw.lower())
    group_order: list[str] = []
    groups: dict[str, list[tuple[str, str | None]]] = {}
    for sym, ex in ordered_pairs:
        if group_by_key == 'cluster':
            gval = infer_symbol_cluster(sym)
        else:
            row = row_by_symbol.get(sym, {})
            if group_col:
                raw = row.get(group_col, '')
            else:
                raw = row.get(group_by_raw, '')
            gval = str(raw).strip() if raw is not None else ''
            if not gval:
                gval = 'ungrouped'
        gname = f'{group_by_key}_{_safe_name(gval)}'
        if gname not in groups:
            groups[gname] = []
            group_order.append(gname)
        groups[gname].append((sym, ex))
    min_size = max(1, int(min_symbols_per_group))
    out: list[tuple[str, list[tuple[str, str | None]]]] = []
    for gname in group_order:
        members = groups.get(gname, [])
        if len(members) < min_size:
            continue
        out.append((gname, members))
    if not out:
        raise ValueError(f'no symbol groups satisfy min_symbols_per_group={min_size} for group_by={group_by_key}')
    return out
