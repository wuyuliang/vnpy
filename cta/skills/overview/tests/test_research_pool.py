"""research_pool.py 单元测试."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from cta.skills.overview.research_pool import (
    FEATURE_DIR,
    ResearchPool,
    build_research_pool,
    in_research_pool,
    reset_cache,
    resolve_research_symbols,
)


def _write_pool_yaml(tmpdir: Path, a_max: int = 5, b_max: int = 10) -> Path:
    cfg = {
        "tiers": {
            "A": {"max_rank": a_max, "description": "A 档"},
            "B": {"max_rank": b_max, "description": "B 档"},
        },
        "explicit_include": [],
        "explicit_exclude": [],
        "intervals": {
            "A": ["day", "minute60"],
            "B": ["day"],
        },
        "coverage": {"min_feature_ratio": 0.8},
    }
    p = tmpdir / "research_pool.yaml"
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
    return p


class TestResearchPoolBuild(unittest.TestCase):
    def setUp(self) -> None:
        reset_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        reset_cache()
        self.tmp.cleanup()

    def test_default_build(self) -> None:
        pool = build_research_pool()
        self.assertIsInstance(pool, ResearchPool)
        # A 档至少 RB0 在
        self.assertIn("RB0", pool.symbols("A"))
        # ranking 升序
        ranks = [m.research_rank for m in pool.members("A")]
        self.assertEqual(ranks, sorted(ranks))

    def test_custom_yaml(self) -> None:
        path = _write_pool_yaml(self.tmpdir, a_max=3, b_max=5)
        pool = build_research_pool(str(path))
        self.assertEqual(len(pool.members("A")), 3)
        # B 档 = 排名 4-5（排除 A 里的那 3 个）
        self.assertEqual(len(pool.members("B")), 2)

    def test_invalid_b_max(self) -> None:
        path = _write_pool_yaml(self.tmpdir, a_max=10, b_max=5)
        reset_cache()
        with self.assertRaises(ValueError):
            build_research_pool(str(path))

    def test_intervals_accessor(self) -> None:
        path = _write_pool_yaml(self.tmpdir)
        pool = build_research_pool(str(path))
        self.assertIn("day", pool.intervals("A"))
        self.assertIn("minute60", pool.intervals("A"))
        self.assertEqual(pool.intervals("B"), ["day"])

    def test_bad_interval_name(self) -> None:
        bad = {
            "tiers": {"A": {"max_rank": 3}, "B": {"max_rank": 5}},
            "intervals": {"A": ["minute7"], "B": ["day"]},  # 非法
        }
        p = self.tmpdir / "bad.yaml"
        with open(p, "w", encoding="utf-8") as fh:
            yaml.safe_dump(bad, fh)
        reset_cache()
        with self.assertRaises(ValueError):
            build_research_pool(str(p))


class TestPublicAPI(unittest.TestCase):
    def setUp(self) -> None:
        reset_cache()

    def tearDown(self) -> None:
        reset_cache()

    def test_in_research_pool(self) -> None:
        self.assertTrue(in_research_pool("RB0", tier="A"))

    def test_resolve_research_symbols_no_filter(self) -> None:
        lst = resolve_research_symbols("A")
        self.assertGreater(len(lst), 0)
        keys = set(lst[0].keys())
        self.assertIn("symbol", keys)
        self.assertIn("research_rank", keys)
        # 升序
        ranks = [x["research_rank"] for x in lst]
        self.assertEqual(ranks, sorted(ranks))

    def test_resolve_research_symbols_with_interval(self) -> None:
        lst = resolve_research_symbols("A", interval="day")
        for x in lst:
            self.assertEqual(x["interval"], "day")

    def test_require_feature_returns_subset(self) -> None:
        full = resolve_research_symbols("A", interval="day", require_feature=False)
        only_cov = resolve_research_symbols("A", interval="day", require_feature=True)
        self.assertLessEqual(len(only_cov), len(full))
        # 若当前 feature 尚未生成 -> only_cov 可能为空；不强制非空
        if only_cov:
            sym = only_cov[0]["symbol"]
            d = FEATURE_DIR / "day" / sym
            self.assertTrue(any(d.glob("*.parquet")),
                            f"require_feature=True 应保证 {d} 下有 parquet")

    def test_resolve_a_plus_b(self) -> None:
        a = resolve_research_symbols("A")
        ab = resolve_research_symbols("A+B")
        self.assertGreaterEqual(len(ab), len(a))


if __name__ == "__main__":
    unittest.main()
