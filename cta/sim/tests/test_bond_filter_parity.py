"""Bond trade-filter parity tests between sim EntryGateChain and OOT gate (P0Δ-2)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.oot_trade_filter_gate import apply_trade_filter_gate
from cta.sim.adapters.entry_gate_chain import EntryGateChain


def _bond_candidates(n: int = 100) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for i in range(n):
        p = (i % 20) / 20.0
        # 避免单行 DataFrame 的 `max<=1` 百分位缩放分支，统一使用 >1 的 pctl。
        pctl = float(10 + (i % 90))
        rows.append(
            {
                "symbol": "T0",
                "exchange": "CFFEX",
                "interval": "day",
                "cluster": "bond",
                "side": "long" if i % 2 == 0 else "short",
                "signal_type": "donchian_breakout",
                "trade_filter_prob": p,
                "trade_filter_prob_pctl": pctl,
            }
        )
    return pd.DataFrame(rows)


class TestBondFilterParity(unittest.TestCase):
    def test_entry_gate_chain_matches_oot_trade_filter_gate(self) -> None:
        cfg = OotEvaluationConfig(
            use_portfolio_logic_runtime=False,
            use_trade_filter_gate=True,
            trade_filter_gate_mode="cluster_interval_percentile",
            signal_type_blacklist=(),
        )
        df = _bond_candidates(100)
        gate_by_legacy = pd.Series([True] * len(df), index=df.index, dtype=bool)
        block_reason = pd.Series([""] * len(df), index=df.index, dtype=object)
        _, oot_gate, oot_reason = apply_trade_filter_gate(
            df,
            cfg=cfg,
            gate_by_legacy=gate_by_legacy,
            model_block_reason=block_reason,
        )
        chain = EntryGateChain(cfg)
        for idx, row in df.iterrows():
            decision = chain.evaluate(row.to_dict(), dt=pd.Timestamp("2026-01-02"))
            self.assertEqual(decision.passed, bool(oot_gate.loc[idx]))
            if decision.passed:
                self.assertEqual(decision.block_reason, "")
            else:
                self.assertEqual(decision.block_reason, str(oot_reason.loc[idx]))
                self.assertEqual(decision.block_stage, "trade_filter")


if __name__ == "__main__":
    unittest.main()
