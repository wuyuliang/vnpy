"""iteration.py 单元测试（所有写操作都在临时目录，不污染 cta/report/ideas/）."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cta.skills.overview.iteration import (
    IdeaRecord,
    advance_stage,
    list_open_ideas,
    record_idea,
)


class TestIdeaRecord(unittest.TestCase):
    def test_valid(self) -> None:
        r = IdeaRecord(
            id="20260420_tight_range",
            name="Tight Range",
            stage="hypothesize",
            created_at="2026-04-20",
        )
        self.assertEqual(r.id, "20260420_tight_range")

    def test_bad_id_format(self) -> None:
        with self.assertRaises(ValueError):
            IdeaRecord(id="bad_id", name="x",
                       stage="hypothesize", created_at="2026-04-20")

    def test_bad_stage(self) -> None:
        with self.assertRaises(ValueError):
            IdeaRecord(id="20260420_x", name="x",
                       stage="unknown", created_at="2026-04-20")

    def test_bad_date(self) -> None:
        with self.assertRaises(ValueError):
            IdeaRecord(id="20260420_x", name="x",
                       stage="hypothesize", created_at="2026/04/20")


class TestRecordIdea(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_and_read(self) -> None:
        rec = record_idea(
            "Tight Range Breakout",
            owner="wu",
            root=self.root,
            created_at="2026-04-20",
            next_action="run IC",
        )
        self.assertEqual(rec.id, "20260420_tight_range_breakout")
        folder = self.root / rec.id
        self.assertTrue(folder.exists())
        self.assertTrue((folder / "meta.yaml").exists())
        self.assertTrue((folder / "hypothesis.md").exists())
        # hypothesis 模板含 owner / created_at
        content = (folder / "hypothesis.md").read_text(encoding="utf-8")
        self.assertIn("Tight Range Breakout", content)
        self.assertIn("wu", content)

    def test_slugify_mixed_name(self) -> None:
        rec = record_idea(
            "Volume-Spike & Pullback!!",
            root=self.root, created_at="2026-04-20",
        )
        self.assertEqual(rec.id, "20260420_volume_spike_pullback")

    def test_idempotent(self) -> None:
        r1 = record_idea("X", root=self.root, created_at="2026-04-20")
        r2 = record_idea("X", root=self.root, created_at="2026-04-20")
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(r1.created_at, r2.created_at)

    def test_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            record_idea("!!!", root=self.root, created_at="2026-04-20")


class TestListOpenIdeas(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        record_idea("A", root=self.root, created_at="2026-04-20")
        record_idea("B", root=self.root, created_at="2026-04-21")
        record_idea("C", root=self.root, created_at="2026-04-22", stage="dropped")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_excludes_dropped(self) -> None:
        ideas = list_open_ideas(root=self.root)
        names = [i.name for i in ideas]
        self.assertIn("A", names)
        self.assertIn("B", names)
        self.assertNotIn("C", names)

    def test_include_dropped(self) -> None:
        ideas = list_open_ideas(root=self.root, include_dropped=True)
        names = [i.name for i in ideas]
        self.assertIn("C", names)

    def test_empty_root(self) -> None:
        empty = Path(self.tmp.name) / "_never_created"
        self.assertEqual(list_open_ideas(root=empty), [])


class TestAdvanceStage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rec = record_idea("Alpha", root=self.root, created_at="2026-04-20")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_advance(self) -> None:
        rec = advance_stage(self.rec.id, "backtest", root=self.root,
                            decision="promoted")
        self.assertEqual(rec.stage, "backtest")
        self.assertEqual(rec.decision, "promoted")
        # 重新读列表
        self.assertEqual(
            list_open_ideas(root=self.root)[0].stage, "backtest"
        )

    def test_bad_new_stage(self) -> None:
        with self.assertRaises(ValueError):
            advance_stage(self.rec.id, "nope", root=self.root)  # type: ignore[arg-type]

    def test_missing_idea(self) -> None:
        with self.assertRaises(FileNotFoundError):
            advance_stage("99999999_xxx", "paper", root=self.root)


if __name__ == "__main__":
    unittest.main()
