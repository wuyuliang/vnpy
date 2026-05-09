"""把 ``model_pipeline`` 训出的 ``trade_filter`` 模型挂到
``LegacyCtaAdapter.order_filter``，在仿真 / 实盘里用模型概率拦截开仓。

设计
----
- 模型加载顺序：``joblib.load`` → 失败再 ``pickle.load``（兼容 sklearn 与自定义对象）
- 特征列清单：默认从 sibling ``<model_path 去后缀>_features.csv`` 读取（与
  ``model_pipeline`` 输出布局一致）；用户可显式传 ``feature_columns_csv``
- 在 ``adapter._frame.iloc[-1:]`` 取最新一根 bar 的特征向量；缺列 / 缺 frame 时
  **不拦截** + 写日志，避免上线初期把策略锁死
- **平仓订单**（``side == "flat"``）始终放行，不做模型校验，避免持仓无法离场

集成示例
--------
    from cta.live.model_filter import make_trade_filter
    adapter.order_filter = make_trade_filter(
        "cta/report/backtest/20260509_RB0_60min_model_pipeline/models/trade_filter_xyz.joblib",
        threshold=0.55,
    )

要与 ``cta.live.risk.make_risk_filter`` 同时使用：把两个 filter 串联在一个 lambda 里：
    rf = make_risk_filter(guard, ...)
    mf = make_trade_filter(model_path, threshold=0.55)
    adapter.order_filter = lambda o, a: rf(o, a) and mf(o, a)
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger(__name__)


def _load_model(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"model file not found: {path}")
    # 优先 joblib（sklearn 推荐）；若未装或失败回退 pickle
    try:
        import joblib  # type: ignore
        return joblib.load(p)
    except Exception:
        pass
    with open(p, "rb") as f:
        return pickle.load(f)


def _resolve_features_csv(model_path: str) -> str:
    p = Path(model_path)
    stem = p.stem
    # sibling: <stem>_features.csv
    candidate = p.with_name(f"{stem}_features.csv")
    return str(candidate)


def _read_feature_names(csv_path: str) -> list[str]:
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    if df.empty:
        return []
    # 兼容首列为特征名 / 含 'name' / 含 'feature' 列三种格式
    for cand in ("name", "feature", "feature_name"):
        if cand in df.columns:
            return df[cand].astype(str).tolist()
    return df.iloc[:, 0].astype(str).tolist()


def make_trade_filter(
    model_path: str,
    *,
    feature_columns_csv: str | None = None,
    threshold: float = 0.5,
    proba_index: int = 1,
) -> Callable[[dict, Any], bool]:
    """加载 trade_filter 模型并返回 ``adapter.order_filter`` 兼容的 callable。"""
    model = _load_model(model_path)
    fc = feature_columns_csv or _resolve_features_csv(model_path)
    if not Path(fc).exists():
        raise FileNotFoundError(
            f"feature columns CSV not found: {fc} (set feature_columns_csv= to override)"
        )
    columns = _read_feature_names(fc)
    if not columns:
        raise ValueError(f"feature columns CSV empty: {fc}")
    th = float(threshold)
    pi = int(proba_index)

    def _log(adapter: Any, msg: str) -> None:
        log = getattr(adapter, "write_log", None)
        if callable(log):
            log(msg)
        else:
            logger.info(msg)

    def _filter(order: dict, adapter: Any) -> bool:
        side = str(order.get("side", "")).lower()
        if side == "flat":
            return True
        frame = getattr(adapter, "_frame", None)
        if frame is None or len(frame) == 0:
            _log(adapter, "model_filter: no frame (warmup); allowing pass-through")
            return True
        try:
            row = frame.iloc[-1:]
            X = row.reindex(columns=columns)
            if X.isna().any().any():
                missing = [c for c in columns if c not in row.columns or row[c].isna().all()]
                _log(adapter, f"model_filter: missing/null columns {missing[:5]}; allow")
                return True
        except KeyError as e:
            _log(adapter, f"model_filter: missing column {e}; allow")
            return True
        try:
            prob = float(model.predict_proba(X)[:, pi][0])
        except Exception as e:  # noqa: BLE001
            _log(adapter, f"model_filter: predict_proba error {e}; allow")
            return True
        passed = prob >= th
        if not passed:
            _log(adapter, f"model_filter: prob={prob:.4f} < {th}, blocking")
        return passed

    return _filter


__all__ = ["make_trade_filter"]
