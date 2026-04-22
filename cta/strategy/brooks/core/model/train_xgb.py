"""XGBoost 训练脚本。

CLI:
    python3 -m cta.strategy.brooks.core.model.train_xgb \
        --top-n 8 --start 2018-01-01 --end 2022-12-31 \
        --target-rr 2.0 --target-bars 20 \
        --out-dir cta/strategy/brooks/models

输出:
    <out_dir>/xgb_<YYYYMMDD_HHMMSS>.ubj      # 模型
    <out_dir>/xgb_<YYYYMMDD_HHMMSS>.meta.json  # 特征列 + 切分区间 + AUC + 阈值建议
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from cta.strategy.brooks.config.params import load_params
from cta.strategy.brooks.config.symbols import resolve_symbols
from cta.strategy.brooks.core.features.adapter import FeatureAdapter
from cta.strategy.brooks.core.model.dataset import build_dataset

logger = logging.getLogger(__name__)


def _train(params, ds, out_dir: Path) -> Path:
    from sklearn.metrics import roc_auc_score
    from xgboost import XGBClassifier

    splits = params.model.train.splits
    train_ds, val_ds, test_ds = ds.time_split(splits.train_end, splits.val_end)
    logger.info("splits: train=%d val=%d test=%d",
                len(train_ds), len(val_ds), len(test_ds))

    if len(train_ds) < 50 or len(val_ds) < 20:
        logger.warning("训练/验证样本过少(train=%d val=%d),结果不可靠",
                       len(train_ds), len(val_ds))

    # 填充 NaN
    X_train = train_ds.X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    X_val = val_ds.X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    X_test = test_ds.X.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    xgb_cfg = params.model.train.xgb
    model = XGBClassifier(
        n_estimators=xgb_cfg.n_estimators,
        max_depth=xgb_cfg.max_depth,
        learning_rate=xgb_cfg.learning_rate,
        subsample=xgb_cfg.subsample,
        colsample_bytree=xgb_cfg.colsample_bytree,
        min_child_weight=xgb_cfg.min_child_weight,
        reg_lambda=xgb_cfg.reg_lambda,
        eval_metric=xgb_cfg.eval_metric,
        early_stopping_rounds=xgb_cfg.early_stopping_rounds,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(
        X_train, train_ds.y,
        eval_set=[(X_val, val_ds.y)] if len(X_val) else None,
        verbose=False,
    )

    metrics = {}
    if len(X_val):
        val_prob = model.predict_proba(X_val)[:, 1]
        metrics["val_auc"] = float(roc_auc_score(val_ds.y, val_prob))
    if len(X_test):
        test_prob = model.predict_proba(X_test)[:, 1]
        metrics["test_auc"] = float(roc_auc_score(test_ds.y, test_prob))

    # 产物路径
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = out_dir / f"xgb_{ts_str}.ubj"
    meta_path = out_dir / f"xgb_{ts_str}.meta.json"

    model.save_model(str(model_path))
    meta = {
        "timestamp": ts_str,
        "feature_cols": ds.feature_cols,
        "splits": {"train_end": splits.train_end, "val_end": splits.val_end},
        "metrics": metrics,
        "n_train": len(train_ds),
        "n_val": len(val_ds),
        "n_test": len(test_ds),
        "pos_rate_train": float(train_ds.y.mean()) if len(train_ds) else 0.0,
        "pos_rate_val": float(val_ds.y.mean()) if len(val_ds) else 0.0,
        "target_rr": params.model.target_rr,
        "target_bars": params.model.target_bars,
        "threshold_recommend": params.model.threshold,
    }
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    logger.info("saved model %s  meta=%s", model_path, metrics)
    return model_path


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--max-tier", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--target-rr", type=float, default=None)
    parser.add_argument("--target-bars", type=int, default=None)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    params = load_params(args.config)

    if args.top_n is not None:
        params.symbols.top_n = args.top_n
    if args.max_tier is not None:
        params.symbols.max_tier = args.max_tier
    if args.target_rr is not None:
        params.model.target_rr = args.target_rr
    if args.target_bars is not None:
        params.model.target_bars = args.target_bars

    vt_symbols = resolve_symbols(params.symbols)
    if not vt_symbols:
        raise SystemExit("未解析出任何可用品种;检查 require_feature_interval")
    logger.info("training universe: %s", vt_symbols)

    adapter = FeatureAdapter(mode="offline")
    ds = build_dataset(
        vt_symbols=vt_symbols,
        params=params,
        start_date=args.start,
        end_date=args.end,
        adapter=adapter,
    )
    if len(ds) == 0:
        raise SystemExit("未提取到任何样本;检查 signal 阈值或样本周期")
    logger.info("dataset: %d samples, pos_rate=%.3f", len(ds), ds.y.mean())

    out_dir = Path(args.out_dir) if args.out_dir else Path(params.output.models_root)
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    _train(params, ds, out_dir)


if __name__ == "__main__":
    main()
