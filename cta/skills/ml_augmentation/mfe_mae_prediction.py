"""§09-03 MFE/MAE prediction helpers."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MFEMAEConfig:
    horizon: int = 40
    atr_col: str = "atr_14"
    quantiles: tuple[float, ...] = (0.2, 0.5, 0.8)


def build_mfe_mae_labels(
    bars: pd.DataFrame,
    signal_index: pd.Index,
    cfg: MFEMAEConfig,
) -> pd.DataFrame:
    """Build MFE/MAE labels in ATR units."""
    need = {"high", "low", "close"}
    miss = need - set(bars.columns)
    if miss:
        raise KeyError(f"build_mfe_mae_labels 缺少列: {miss}")
    atr = bars[cfg.atr_col].astype(float) if cfg.atr_col in bars.columns else (bars["high"] - bars["low"]).astype(float)
    atr = atr.replace(0.0, np.nan).ffill().bfill().fillna(1.0)
    h = max(int(cfg.horizon), 1)

    rows: list[dict] = []
    for idx in signal_index:
        i = int(idx)
        if i < 0 or i >= len(bars):
            continue
        entry = float(bars["close"].iloc[i])
        end = min(len(bars) - 1, i + h)
        future = bars.iloc[i + 1 : end + 1]
        if future.empty:
            mfe = 0.0
            mae = 0.0
        else:
            mfe_abs = float(future["high"].max()) - entry
            mae_abs = entry - float(future["low"].min())
            unit = max(float(atr.iloc[i]), 1e-9)
            mfe = mfe_abs / unit
            mae = mae_abs / unit
        rows.append({"signal_i": i, "mfe": float(mfe), "mae": float(mae)})
    return pd.DataFrame(rows)


def train_quantile_models(
    X: pd.DataFrame,
    y_mfe: pd.Series,
    y_mae: pd.Series,
    cfg: MFEMAEConfig,
) -> dict:
    """
    Train dependency-free quantile proxy models.

    The model stores global quantiles plus a tiny linear adjustment anchor.
    """
    Xn = X.select_dtypes(include=[np.number]).fillna(0.0)
    q = tuple(float(v) for v in cfg.quantiles)
    mfe_q = {qq: float(np.nanquantile(y_mfe.astype(float), qq)) for qq in q}
    mae_q = {qq: float(np.nanquantile(y_mae.astype(float), qq)) for qq in q}
    mean_feat = {str(k): float(v) for k, v in Xn.mean(axis=0).to_dict().items()}
    return {
        "quantiles": q,
        "mfe_q": mfe_q,
        "mae_q": mae_q,
        "feature_mean": mean_feat,
    }


def predict_mfe_mae(
    models: dict,
    feat: pd.Series,
) -> dict[str, float]:
    """Return mfe_q*/mae_q* predictions in ATR units."""
    q = tuple(models.get("quantiles", (0.2, 0.5, 0.8)))
    mfe_q = dict(models.get("mfe_q", {}))
    mae_q = dict(models.get("mae_q", {}))
    mean = dict(models.get("feature_mean", {}))

    if len(feat) == 0:
        adj = 0.0
    else:
        delta = 0.0
        n = 0
        for k, v in feat.items():
            if k in mean:
                delta += float(v) - float(mean[k])
                n += 1
        adj = 0.05 * (delta / max(n, 1))

    out: dict[str, float] = {}
    for qq in q:
        suf = int(round(float(qq) * 100))
        out[f"mfe_q{suf}"] = max(0.0, float(mfe_q.get(qq, 0.0) + adj))
        out[f"mae_q{suf}"] = max(0.0, float(mae_q.get(qq, 0.0) + abs(adj) * 0.5))
    return out
