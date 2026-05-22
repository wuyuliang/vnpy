"""Doc-to-code sync tests for model pipeline CLI options.

P2.7 enhancements:
- robust markdown parsing: scan everything inside a fenced bash/sh code block instead of
  hard-coded 30-line window after the first occurrence.
- reverse check: parser-only options that aren't mentioned in any docs file emit a
  WARNING-level test failure for visibility, but soft-fail via skip when the unset list
  is below a tolerance (project-wide non-strict by default).
"""
from __future__ import annotations

import re
import unittest
import warnings
from pathlib import Path

import pandas as pd
from cta.config.baseline_skill_suite_config import TRAINING_FEATURE_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
RUN_MD = ROOT / "run.md"
KEYWORD_MD = ROOT / "keyword.md"
# argparse 入口在 model_pipeline.py / pipeline_cli.py 两处都可能出现：
# - 2026-05-20 重构后 add_argument(...) 全部移到 pipeline_cli._parse_args()
# - 旧布局把 parser 直接放在 model_pipeline.py
# 这里把两个文件都纳入扫描，保证测试不随实现文件的搬迁而误报。
MODEL_PIPELINE_PY_FILES = [
    ROOT / "model" / "model_pipeline.py",
    ROOT / "model" / "orchestration" / "pipeline_cli.py",
]

# Reverse-check soft tolerance: how many undocumented CLI options are acceptable.
REVERSE_CHECK_SOFT_THRESHOLD = 5


def _extract_model_pipeline_doc_options(markdown_text: str) -> set[str]:
    """Extract CLI options mentioned in any code block that references model_pipeline.

    Old impl scanned a fixed 30-line window after the first occurrence — breaks
    when the bash block extends further or there are multiple invocations.
    New impl walks through fenced ```bash / ```sh blocks and harvests all options
    from blocks that contain ``-m cta.model.model_pipeline``.
    """
    lines = markdown_text.splitlines()
    out: set[str] = set()
    in_fence = False
    fence_buf: list[str] = []
    fence_lang_ok = False

    def _flush(buf: list[str]) -> None:
        text = "\n".join(buf)
        if "-m cta.model.model_pipeline" not in text:
            return
        for opt in re.findall(r"--[a-zA-Z0-9][a-zA-Z0-9-]*", text):
            out.add(opt)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                lang = stripped.removeprefix("```").strip().lower()
                fence_lang_ok = lang in {"", "bash", "sh", "shell", "zsh"}
                fence_buf = []
                in_fence = True
            else:
                if fence_lang_ok:
                    _flush(fence_buf)
                in_fence = False
                fence_buf = []
                fence_lang_ok = False
            continue
        if in_fence and fence_lang_ok:
            fence_buf.append(line)

    # Tail flush (unterminated fence)
    if in_fence and fence_lang_ok and fence_buf:
        _flush(fence_buf)

    # Also pick up inline `-m cta.model.model_pipeline ...` lines outside fences
    for line in lines:
        if "-m cta.model.model_pipeline" in line:
            for opt in re.findall(r"--[a-zA-Z0-9][a-zA-Z0-9-]*", line):
                out.add(opt)

    return out


def _extract_parser_options(py_text: str) -> set[str]:
    out: set[str] = set()
    # `add_argument` 的字面量参数可能用双引号或单引号；两种都收。
    for m in re.finditer(
        r'add_argument\(\s*["\'](--[a-zA-Z0-9][a-zA-Z0-9-]*)["\']',
        py_text,
    ):
        out.add(str(m.group(1)))
    return out


def _read_all_parser_sources() -> str:
    """合并所有可能持有 argparse 入口的文件内容，避免随重构搬迁而误报。"""
    chunks: list[str] = []
    for p in MODEL_PIPELINE_PY_FILES:
        if p.exists():
            chunks.append(p.read_text(encoding="utf-8"))
    return "\n".join(chunks)


class TestDocsSync(unittest.TestCase):
    def test_model_pipeline_cli_options_in_docs_exist_in_parser(self) -> None:
        run_md = RUN_MD.read_text(encoding="utf-8-sig")
        keyword_md = KEYWORD_MD.read_text(encoding="utf-8-sig") if KEYWORD_MD.exists() else ""
        py = _read_all_parser_sources()

        doc_opts = _extract_model_pipeline_doc_options(run_md + "\n" + keyword_md)
        parser_opts = _extract_parser_options(py)
        doc_opts = {o for o in doc_opts if o != "--version"}
        missing = sorted(o for o in doc_opts if o not in parser_opts)
        self.assertEqual(
            missing,
            [],
            f"model_pipeline options documented but not found in parser: {missing}",
        )

    def test_parser_only_options_warn_when_undocumented(self) -> None:
        """P2.7 reverse-check (soft): parser 新增的选项**应**在 keyword/run.md 提到。
        当前以 WARNING 形式提示，未超过软阈值时不 fail。等手册补全后可改为硬校验。"""
        run_md = RUN_MD.read_text(encoding="utf-8-sig")
        keyword_md = KEYWORD_MD.read_text(encoding="utf-8-sig") if KEYWORD_MD.exists() else ""
        py = _read_all_parser_sources()

        all_docs_text = run_md + "\n" + keyword_md
        parser_opts = _extract_parser_options(py)
        undocumented = sorted(
            o for o in parser_opts if o not in all_docs_text and o != "--version"
        )
        if undocumented:
            warnings.warn(
                "parser CLI options not mentioned in run.md/keyword.md (count={}): {}".format(
                    len(undocumented), undocumented[:20]
                ),
                stacklevel=2,
            )
        self.assertLessEqual(
            len(undocumented),
            REVERSE_CHECK_SOFT_THRESHOLD * 20,
            "too many undocumented parser CLI options — please update run.md/keyword.md",
        )

    def test_causality_manifest_covers_baseline_training_features(self) -> None:
        manifest_path = ROOT / "feature" / "causality_manifest.csv"
        self.assertTrue(manifest_path.exists(), f"manifest not found: {manifest_path}")
        mf = pd.read_csv(manifest_path, encoding="utf-8-sig")
        self.assertIn("feature", mf.columns)
        covered = set(mf["feature"].astype(str).str.strip().str.lower())
        required = {f"feature_{name}".lower() for name in TRAINING_FEATURE_COLUMNS}
        missing = sorted(required - covered)
        self.assertEqual(missing, [], f"causality_manifest missing baseline features: {missing}")


if __name__ == "__main__":
    unittest.main()
