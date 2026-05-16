"""Sanity checks for cta/run.sh custom actions."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_SH = ROOT / "run.sh"


class TestRunScriptActions(unittest.TestCase):
    def test_has_index_bond_data_step_function(self) -> None:
        text = RUN_SH.read_text(encoding="utf-8")
        self.assertIn("step_data_index_bond()", text)

    def test_case_includes_index_bond_data_action(self) -> None:
        text = RUN_SH.read_text(encoding="utf-8")
        self.assertRegex(text, r"\n\s*data_index_bond\)\s+step_data_index_bond\s*;;")

    def test_index_bond_step_uses_expected_download_flags(self) -> None:
        text = RUN_SH.read_text(encoding="utf-8")
        m = re.search(
            r"step_data_index_bond\(\)\s*\{(?P<body>.*?)\n\}",
            text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "step_data_index_bond body not found")
        body = m.group("body")
        self.assertIn("--only-symbols", body)
        self.assertIn("${INDEX_BOND_SYMBOLS}", body)
        self.assertIn("--include-financial", body)
        self.assertIn("--include-index", body)
        self.assertIn("--build-macro", body)

    def test_index_bond_symbols_default_contains_index_and_bond_prefixes(self) -> None:
        text = RUN_SH.read_text(encoding="utf-8")
        m = re.search(r'INDEX_BOND_SYMBOLS="\$\{INDEX_BOND_SYMBOLS:-([^"]+)\}"', text)
        self.assertIsNotNone(m)
        defaults = set(str(m.group(1)).split())
        self.assertIn("IF0", defaults)
        self.assertIn("T0", defaults)


if __name__ == "__main__":
    unittest.main()
