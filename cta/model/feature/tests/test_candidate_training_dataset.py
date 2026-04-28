"""Tests for candidate-event training dataset builder."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.model.feature.candidate_training_dataset import (
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _parse_args,
    build_and_save_candidate_training_dataset,
    standardize_candidate_events,
)


class TestCandidateTrainingDataset(unittest.TestCase):
    def test_normalize_intervals_supports_mixed_tokens_and_dedup(self) -> None:
        out = _normalize_intervals(["day,60min", " 30min ", "60min", "15min,5min", "min"])
        self.assertEqual(out, ("day", "60min", "30min", "15min", "5min", "min"))

    def test_normalize_intervals_raises_when_empty(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_intervals(["", " , ", "   "])

    def test_parse_args_supports_top_n_symbols_and_multi_intervals(self) -> None:
        args = _parse_args(
            [
                "--top-n-symbols",
                "3",
                "--symbols-ranking-path",
                "cta/feature/symbols_research_ranking.csv",
                "--interval",
                "day,60min",
                "30min,15min",
                "--trade-side-mode",
                "both",
            ]
        )
        self.assertEqual(int(args.top_n_symbols), 3)
        self.assertEqual(
            tuple(args.interval),
            ("day,60min", "30min,15min"),
        )
        self.assertEqual(str(args.symbols_ranking_path), "cta/feature/symbols_research_ranking.csv")

    def test_load_top_n_symbols_from_ranking_orders_by_research_rank(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_rank_topn_") as td:
            path = Path(td) / "symbols_research_ranking.csv"
            pd.DataFrame(
                {
                    "symbol": ["CU0", "RB0", "AU0", "RB0"],
                    "exchange": ["SHFE", "SHFE", "SHFE", "SHFE"],
                    "research_rank": [3, 1, 2, 99],
                }
            ).to_csv(path, index=False, encoding="utf-8-sig")

            out = _load_top_n_symbols_from_ranking(path, top_n=3)
            self.assertEqual(out, [("RB0", "SHFE"), ("AU0", "SHFE"), ("CU0", "SHFE")])

    def test_standardize_candidate_events_maps_status_and_labels(self) -> None:
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["minute60", "minute60", "minute60"],
                "datetime": pd.to_datetime(
                    ["2019-01-02 09:00:00", "2019-01-02 10:00:00", "2019-01-02 11:00:00"]
                ),
                "signal_datetime": pd.to_datetime(
                    ["2019-01-02 08:00:00", "2019-01-02 09:00:00", "2019-01-02 10:00:00"]
                ),
                "signal_type": ["donchian_breakout", "atr_breakout", "tight_range_breakout"],
                "side": ["long", "short", "long"],
                "candidate_status": ["filled", "filtered", "not_triggered"],
                "filtered_reason": ["", "quality_gate", ""],
                "trigger": [3510.0, 3500.0, 3515.0],
                "entry_price": [3510.0, float("nan"), float("nan")],
                "stop_price": [3508.0, 3450.0, 3525.0],
                "future_mfe_atr": [1.00, 0.40, 0.10],
                "future_mae_atr": [0.50, 0.20, 0.30],
                "atr_warmed": [1, 1, 1],
                "feature_close": [3510.0, 3505.0, 3512.0],
                "feature_atr14": [20.0, 21.0, 22.0],
            }
        )

        out = standardize_candidate_events(candidate)
        self.assertEqual(len(out), 3)
        for col in (
            "candidate_id",
            "setup_type",
            "direction",
            "sample_status",
            "block_reason",
            "entry_price_virtual",
            "stop_price_virtual",
            "future_return_atr",
            "executed_flag",
            "is_good_opportunity",
            "opportunity_class",
        ):
            self.assertIn(col, out.columns)

        # C4: not_triggered must map to its own status, NOT blocked_by_execution.
        self.assertEqual(
            list(out["sample_status"]),
            ["executed", "filtered_by_rule", "not_triggered_market"],
        )
        self.assertEqual(list(out["executed_flag"]), [1, 0, 0])
        self.assertEqual(out.loc[1, "block_reason"], "quality_gate")
        self.assertEqual(out.loc[2, "block_reason"], "next_bar_not_triggered")
        # C2: when entry_price is NaN, fall back to trigger (3500), NEVER stop_price (3450).
        self.assertAlmostEqual(float(out.loc[1, "entry_price_virtual"]), 3500.0, places=6)
        # 文档要求机会质量标签独立于是否成交，filtered 样本也允许成为好机会。
        self.assertEqual(int(out.loc[1, "is_good_opportunity"]), 1)

    def test_build_and_save_candidate_training_dataset_merges_generic_features(self) -> None:
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "signal_datetime": pd.to_datetime(["2019-01-02 08:00:00", "2019-01-02 09:00:00"]),
                "signal_type": ["donchian_breakout", "atr_breakout"],
                "side": ["long", "short"],
                "candidate_status": ["filled", "filtered"],
                "filtered_reason": ["", "trend_filter_failed"],
                "trigger": [3510.0, 3500.0],
                "entry_price": [3510.0, float("nan")],
                "stop_price": [3508.0, 3450.0],
                "future_mfe_atr": [0.9, 0.4],
                "future_mae_atr": [0.2, 0.3],
                "atr_warmed": [1, 1],
                "feature_close": [3510.0, 3505.0],
                "feature_atr14": [20.0, 21.0],
            }
        )

        with tempfile.TemporaryDirectory(prefix="cta_candidate_ds_") as td:
            root = Path(td)
            feature_root = root / "feature"
            generic_dir = feature_root / "minute60" / "RB0"
            generic_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                    "sma_20": [3490.0, 3495.0],
                    "rsi_14": [55.0, 58.0],
                }
            ).to_parquet(generic_dir / "2019-01-02.parquet", index=False)

            out = build_and_save_candidate_training_dataset(
                candidate_df=candidate,
                symbol="RB0",
                interval="60min",
                output_root=root / "model_feature",
                feature_root=feature_root,
                run_tag="20260427",
                generic_columns=("sma_20", "rsi_14"),
            )

            self.assertTrue(out.candidate_events_parquet.exists())
            self.assertTrue(out.training_samples_parquet.exists())
            self.assertTrue(out.summary_csv.exists())

            merged = pd.read_parquet(out.training_samples_parquet)
            self.assertEqual(len(merged), 2)
            self.assertIn("feature_close", merged.columns)
            self.assertIn("generic_sma_20", merged.columns)
            self.assertIn("generic_rsi_14", merged.columns)
            self.assertIn("sample_status", merged.columns)

    # ------------------ C1 + C12: candidate_id 在两个落盘文件之间必须一致 -------
    def test_candidate_id_stable_between_candidate_events_and_training_samples(self) -> None:
        """C1: 即便 merge_asof 重排了行序，candidate_events.parquet 与
        training_samples.parquet 的 candidate_id 集合必须完全一致，
        否则下游做 7.1-7.4 四类样本分析无法 join。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["minute60", "minute60", "minute60"],
                # 顺序故意打乱，让 merge_asof 的 sort_values 一定改变行序
                "datetime": pd.to_datetime(
                    ["2019-01-02 11:00:00", "2019-01-02 09:00:00", "2019-01-02 10:00:00"]
                ),
                "signal_type": ["atr_breakout", "donchian_breakout", "tight_range_breakout"],
                "side": ["short", "long", "long"],
                "candidate_status": ["filled", "filled", "filtered"],
                "filtered_reason": ["", "", "trend_filter_failed"],
                "trigger": [3520.0, 3510.0, 3515.0],
                "entry_price": [3520.0, 3510.0, float("nan")],
                "stop_price": [3530.0, 3500.0, 3525.0],
                "future_mfe_atr": [0.8, 0.9, 0.4],
                "future_mae_atr": [0.3, 0.2, 0.3],
                "atr_warmed": [1, 1, 1],
                "feature_close": [3520.0, 3510.0, 3512.0],
            }
        )
        with tempfile.TemporaryDirectory(prefix="cta_candidate_id_stable_") as td:
            root = Path(td)
            feature_root = root / "feature"
            d = feature_root / "minute60" / "RB0"
            d.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(
                        ["2019-01-02 09:00:00", "2019-01-02 10:00:00", "2019-01-02 11:00:00"]
                    ),
                    "sma_20": [1.0, 2.0, 3.0],
                }
            ).to_parquet(d / "2019-01-02.parquet", index=False)
            out = build_and_save_candidate_training_dataset(
                candidate_df=candidate,
                symbol="RB0",
                interval="60min",
                output_root=root / "model_feature",
                feature_root=feature_root,
                run_tag="20260427",
                generic_columns=("sma_20",),
            )
            ev = pd.read_parquet(out.candidate_events_parquet)
            tr = pd.read_parquet(out.training_samples_parquet)
            self.assertEqual(set(ev["candidate_id"]), set(tr["candidate_id"]))
            self.assertEqual(len(ev), len(tr))

    # ------------------ C2: entry_price_virtual 不能用 stop_price 当兜底 -------
    def test_entry_price_virtual_falls_back_to_trigger_not_stop_price(self) -> None:
        """C2: 当 entry_price 缺失时，必须用 trigger（突破/触发价）作兜底，
        不能用 stop_price（止损价）。后者在 limit-order/ATR breakout 场景
        与触发价完全不同，会污染未来标签。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_type": ["atr_breakout"],
                "side": ["short"],
                "candidate_status": ["filtered"],
                "filtered_reason": ["quality_gate"],
                "trigger": [3500.0],
                "entry_price": [float("nan")],
                "stop_price": [3450.0],  # 故意离 trigger 50 点远
                "future_mfe_atr": [0.4],
                "future_mae_atr": [0.2],
                "atr_warmed": [1],
                "feature_close": [3505.0],
            }
        )
        out = standardize_candidate_events(candidate)
        self.assertAlmostEqual(float(out.iloc[0]["entry_price_virtual"]), 3500.0, places=6)
        self.assertNotAlmostEqual(float(out.iloc[0]["entry_price_virtual"]), 3450.0, places=6)

    # ------------------ C3: atr_warmed=0 时机会质量标签必须保留为未知 ----------
    def test_is_good_opportunity_unknown_when_atr_not_warmed(self) -> None:
        """C3: atr_warmed=0 的 warmup 期样本 mfe/mae 不可信（atr_v 由 high-low
        兜底，会大幅高估机会质量）。is_good_opportunity 必须为 0 且
        opportunity_class 必须标记为 U（unknown），future_return_atr 应保留为 NaN。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "candidate_status": ["filled", "filled"],
                "filtered_reason": ["", ""],
                "trigger": [3510.0, 3520.0],
                "entry_price": [3510.0, 3520.0],
                "stop_price": [3508.0, 3518.0],
                # 两行 future_mfe/mae 公式上看都是好机会
                "future_mfe_atr": [3.0, 3.0],
                "future_mae_atr": [0.1, 0.1],
                "atr_warmed": [0, 1],  # row 0 处于 warmup 期
                "feature_close": [3510.0, 3520.0],
            }
        )
        out = standardize_candidate_events(candidate)
        self.assertEqual(int(out.iloc[0]["is_good_opportunity"]), 0)
        self.assertEqual(str(out.iloc[0]["opportunity_class"]), "U")
        self.assertTrue(pd.isna(out.iloc[0]["opportunity_score"]))
        # row 1 atr_warmed=1，应当判好
        self.assertEqual(int(out.iloc[1]["is_good_opportunity"]), 1)
        self.assertNotEqual(str(out.iloc[1]["opportunity_class"]), "U")

    # ------------------ C5: future_return_atr 应承载真实 horizon 收益 ----------
    def test_future_return_atr_stores_real_horizon_return_not_score(self) -> None:
        """C5: future_return_atr 应保存 baseline 给的 future_pnl/atr_v 真实
        horizon 收益，而不是 mfe-0.7*mae 的 opportunity score；后者放在
        opportunity_score 列里。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "candidate_status": ["filled"],
                "trigger": [3510.0],
                "entry_price": [3510.0],
                "stop_price": [3508.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.4],
                "future_pnl_atr": [0.55],  # baseline 真实 horizon return
                "atr_warmed": [1],
                "feature_close": [3510.0],
            }
        )
        out = standardize_candidate_events(candidate)
        # opportunity_score = mfe - 0.7*mae = 1.0 - 0.28 = 0.72
        self.assertAlmostEqual(float(out.iloc[0]["opportunity_score"]), 0.72, places=6)
        # future_return_atr 必须保留 baseline 的真实 horizon return
        self.assertAlmostEqual(float(out.iloc[0]["future_return_atr"]), 0.55, places=6)

    # ------------------ C6: filtered_reason 字符串 'nan' 应被识别为缺失 -------
    def test_block_reason_treats_string_nan_as_missing(self) -> None:
        """C6: parquet round-trip 后 NaN 会变成字符串 'nan'，必须当作缺失处理，
        否则 block_reason 会被错误填成 'nan'。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 09:00:00", "2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout", "atr_breakout"],
                "side": ["long", "long"],
                "candidate_status": ["filled", "filtered"],
                # row 0 不该有 reason；row 1 是 filtered 但 reason 是字符串 'nan'
                "filtered_reason": ["nan", "nan"],
                "trigger": [3510.0, 3500.0],
                "entry_price": [3510.0, float("nan")],
                "stop_price": [3508.0, 3450.0],
                "future_mfe_atr": [1.0, 0.4],
                "future_mae_atr": [0.5, 0.2],
                "atr_warmed": [1, 1],
                "feature_close": [3510.0, 3505.0],
            }
        )
        out = standardize_candidate_events(candidate)
        # executed 行的 block_reason 必须空
        self.assertEqual(out.iloc[0]["block_reason"], "")
        # filtered 行的 reason 是 'nan'，应当回退到 status 默认值，而不是写 'nan'
        self.assertNotEqual(out.iloc[1]["block_reason"], "nan")
        self.assertEqual(out.iloc[1]["block_reason"], "filtered_by_rule")

    # ------------------ C7: 重复主键应直接 raise --------------------------------
    def test_standardize_raises_on_duplicate_primary_key(self) -> None:
        """C7: (symbol, interval, datetime, setup_type, direction) 是候选事件
        的业务自然主键，重复说明上游 ETL 出问题了，必须 raise 而不是用
        seq 兜底掩盖。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00", "2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "candidate_status": ["filled", "filled"],
                "trigger": [3510.0, 3510.0],
                "entry_price": [3510.0, 3510.0],
                "stop_price": [3508.0, 3508.0],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.5, 0.5],
                "atr_warmed": [1, 1],
                "feature_close": [3510.0, 3510.0],
            }
        )
        with self.assertRaises(ValueError):
            standardize_candidate_events(candidate)

    # ------------------ C9: label_class / atr_warmed 必须出现在 core_cols -------
    def test_core_cols_include_label_class_and_atr_warmed(self) -> None:
        """C9: label_class（baseline 决策标签）与 atr_warmed（warmup 标志）
        必须作为稳定 schema 的一部分，避免下游模型 schema 校验在新增 trailing
        列时漂移。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["minute60"],
                "datetime": pd.to_datetime(["2019-01-02 10:00:00"]),
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "candidate_status": ["filled"],
                "trigger": [3510.0],
                "entry_price": [3510.0],
                "stop_price": [3508.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.5],
                "label_class": [1],
                "atr_warmed": [1],
                "feature_close": [3510.0],
            }
        )
        out = standardize_candidate_events(candidate)
        cols_first_30 = list(out.columns[:35])
        self.assertIn("label_class", cols_first_30)
        self.assertIn("atr_warmed", cols_first_30)

    # ------------------ C10: 空输入也要写出含 schema 的空 parquet ----------------
    def test_empty_candidate_df_writes_schema_complete_empty_parquet(self) -> None:
        """C10: 空候选输入也要能落盘含核心列的空 parquet，避免下游读取
        时缺失 sample_status / candidate_id 等列。
        """
        empty = pd.DataFrame()
        with tempfile.TemporaryDirectory(prefix="cta_empty_candidate_ds_") as td:
            out = build_and_save_candidate_training_dataset(
                candidate_df=empty,
                symbol="RB0",
                interval="60min",
                output_root=Path(td) / "model_feature",
                run_tag="20260427",
            )
            ev = pd.read_parquet(out.candidate_events_parquet)
            self.assertEqual(len(ev), 0)
            for col in (
                "candidate_id",
                "sample_status",
                "block_reason",
                "is_good_opportunity",
                "opportunity_class",
                "future_return_atr",
                "executed_flag",
            ):
                self.assertIn(col, ev.columns)


    # ---------------- D5: block_reason 兜底必须识别 <NA>/None/none ------------
    def test_block_reason_treats_pdNA_and_none_string_as_missing(self) -> None:
        """D5: 旧实现只 ``replace({"nan": ""})``，pd.NA → "<NA>" 字符串、
        Python None → "None" 字符串都漏网。修复后这些都被识别为缺失，
        会回退到状态默认 reason（如 ``filtered_by_rule``）。
        """
        candidate = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["minute60", "minute60", "minute60"],
                "datetime": pd.to_datetime(
                    ["2019-01-02 09:00:00", "2019-01-02 10:00:00", "2019-01-02 11:00:00"]
                ),
                "setup_type": [
                    "donchian_breakout",
                    "donchian_breakout",
                    "donchian_breakout",
                ],
                "direction": ["long", "long", "long"],
                "candidate_status": ["filtered", "filtered", "filtered"],
                # 三种"伪缺失"形态：pd.NA / Python None / 字符串 "None"
                "block_reason": pd.Series([pd.NA, None, "None"], dtype="object"),
                "atr_warmed": [1, 1, 1],
                "future_mfe_atr": [0.0, 0.0, 0.0],
                "future_mae_atr": [0.0, 0.0, 0.0],
            }
        )
        out = standardize_candidate_events(candidate)
        # filtered_by_rule 状态下 block_reason 缺失必须回退到默认值 "filtered_by_rule"
        for reason in out["block_reason"].astype(str).tolist():
            self.assertEqual(reason, "filtered_by_rule")

    # ---------------- D2: standardize 二次调用 future_return_atr 错位 ----------
    def test_standardize_preserves_future_return_atr_when_input_is_unsorted(self) -> None:
        """D2: 旧实现 ``out["future_return_atr"] = pd.to_numeric(candidate_df["future_return_atr"])``
        在 sort_values 重排后会按 index alignment 把别人家的值贴过来。
        此用例故意 datetime 倒序传入，断言 future_return_atr 跟 row identity 绑定不错位。
        """
        # 注意：故意把 datetime 倒序排，让 sort_values 真的会换行序。
        df = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["minute60", "minute60"],
                "datetime": pd.to_datetime(["2020-01-02 09:00:00", "2020-01-01 09:00:00"]),
                "setup_type": ["donchian_breakout", "donchian_breakout"],
                "direction": ["long", "short"],
                "atr_warmed": [1, 1],
                "future_mfe_atr": [1.0, 0.5],
                "future_mae_atr": [0.2, 0.3],
                "future_return_atr": [99.0, 11.0],  # row0=99 跟 2020-01-02 绑定，row1=11 跟 2020-01-01 绑定
                "candidate_status": ["filled", "filled"],
            }
        )
        out = standardize_candidate_events(df)
        # 标准化后 out 已按 datetime 升序：先 2020-01-01 (应是 11.0)，后 2020-01-02 (应是 99.0)
        early = out.loc[out["datetime"] == pd.Timestamp("2020-01-01 09:00:00")]
        late = out.loc[out["datetime"] == pd.Timestamp("2020-01-02 09:00:00")]
        self.assertEqual(len(early), 1)
        self.assertEqual(len(late), 1)
        self.assertAlmostEqual(float(early["future_return_atr"].iloc[0]), 11.0)
        self.assertAlmostEqual(float(late["future_return_atr"].iloc[0]), 99.0)


if __name__ == "__main__":
    unittest.main()
