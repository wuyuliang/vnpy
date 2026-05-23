"""P3-22: JSON structured logging tests."""
from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

from cta.utils.logging_config import JsonFormatter, setup_structured_logging


class TestJsonFormatter(unittest.TestCase):
    def test_basic_record_to_json(self) -> None:
        fmt = JsonFormatter()
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x.py", lineno=1,
            msg="hello %s", args=("world",), exc_info=None,
        )
        out = fmt.format(rec)
        payload = json.loads(out)
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["msg"], "hello world")
        self.assertEqual(payload["logger"], "test")
        self.assertIn("ts", payload)

    def test_extras_included(self) -> None:
        fmt = JsonFormatter(include_extras=True)
        rec = logging.LogRecord(
            name="t", level=logging.INFO, pathname="x", lineno=1,
            msg="m", args=(), exc_info=None,
        )
        rec.symbol = "RB0"
        rec.trade_pnl = 12.5
        payload = json.loads(fmt.format(rec))
        self.assertEqual(payload["extra"]["symbol"], "RB0")
        self.assertEqual(payload["extra"]["trade_pnl"], 12.5)

    def test_exception_serialized(self) -> None:
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc = sys.exc_info()
        rec = logging.LogRecord(
            name="t", level=logging.ERROR, pathname="x", lineno=1,
            msg="failed", args=(), exc_info=exc,
        )
        payload = json.loads(fmt.format(rec))
        self.assertIn("exc", payload)
        self.assertIn("ValueError", payload["exc"])

    def test_extras_excluded_when_disabled(self) -> None:
        fmt = JsonFormatter(include_extras=False)
        rec = logging.LogRecord(
            name="t", level=logging.INFO, pathname="x", lineno=1,
            msg="m", args=(), exc_info=None,
        )
        rec.custom_field = "x"
        payload = json.loads(fmt.format(rec))
        self.assertNotIn("extra", payload)


class TestSetupStructuredLogging(unittest.TestCase):
    def test_setup_creates_log_dir_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            setup_structured_logging(
                log_dir=log_dir, structured=True,
                stream_to_stdout=False, log_file_name="t.log",
            )
            logger = logging.getLogger("p3_22_test")
            logger.info("hello", extra={"symbol": "RB0"})
            # 强制 flush
            for h in logging.getLogger().handlers:
                h.flush()
            log_file = log_dir / "t.log"
            self.assertTrue(log_file.exists())
            lines = log_file.read_text(encoding="utf-8").strip().split("\n")
            # 解析最后一行 JSON
            last = json.loads(lines[-1])
            self.assertEqual(last["msg"], "hello")
            self.assertEqual(last["extra"]["symbol"], "RB0")

    def test_setup_idempotent_does_not_double_handler(self) -> None:
        # 重复 setup → handler 数量稳定
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_structured_logging(log_dir=tmpdir, stream_to_stdout=False)
            count1 = len(logging.getLogger().handlers)
            setup_structured_logging(log_dir=tmpdir, stream_to_stdout=False)
            count2 = len(logging.getLogger().handlers)
            self.assertEqual(count1, count2)

    def test_setup_console_only_when_no_log_dir(self) -> None:
        setup_structured_logging(log_dir=None, structured=True, stream_to_stdout=True)
        handlers = logging.getLogger().handlers
        self.assertGreaterEqual(len(handlers), 1)
        # 没有 RotatingFileHandler
        from logging.handlers import RotatingFileHandler
        self.assertFalse(any(isinstance(h, RotatingFileHandler) for h in handlers))


if __name__ == "__main__":
    unittest.main()
