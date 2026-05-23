"""Tests for HotReloadableRegistry (P0-3 验收)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cta.live.hot_reloadable_registry import (
    HotReloadableRegistry,
    validate_registry_schema,
)


def _write_registry(path: Path, entries: list[dict]) -> None:
    payload = {
        "run_tag": "20260523",
        "group_by": "cluster",
        "trade_side_mode": "both",
        "intervals": sorted({e["interval"] for e in entries}),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_entry(model_dir: Path, *, interval: str = "day", group: str = "cluster_black") -> dict:
    return {
        "interval": interval,
        "group_name": group,
        "pool_name": f"GRP_{group.upper()}",
        "model_dir": str(model_dir),
        "members": [{"symbol": "RB0", "exchange": "SHFE"}],
    }


class TestRegistrySchemaValidation(unittest.TestCase):
    def test_validate_missing_file(self) -> None:
        report = validate_registry_schema("/nonexistent/path/registry.json")
        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("not found", report.errors[0].message)

    def test_validate_invalid_json(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ not valid json }")
            path = Path(f.name)
        try:
            report = validate_registry_schema(path)
            self.assertFalse(report.is_valid)
            self.assertIn("invalid json", report.errors[0].message)
        finally:
            path.unlink()

    def test_validate_missing_top_level_keys(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"run_tag": "x"}))
            path = Path(f.name)
        try:
            report = validate_registry_schema(path)
            self.assertFalse(report.is_valid)
            error_msgs = [e.message for e in report.errors]
            for needed in ("group_by", "trade_side_mode", "intervals", "entries"):
                self.assertTrue(any(needed in msg for msg in error_msgs))
        finally:
            path.unlink()

    def test_validate_model_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(tmp / "nonexistent_model_dir")])
            report = validate_registry_schema(registry_path)
            self.assertFalse(report.is_valid)
            self.assertTrue(any("model_dir does not exist" in e.message for e in report.errors))

    def test_validate_clean_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "model_dir_real"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])
            report = validate_registry_schema(registry_path)
            self.assertTrue(report.is_valid)
            self.assertEqual(report.entries_count, 1)
            self.assertEqual(report.errors, ())

    def test_validate_empty_entries_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [])
            report = validate_registry_schema(registry_path)
            # entries 空是 warning 不是 error
            self.assertTrue(report.is_valid)
            self.assertGreaterEqual(len(report.warnings), 1)


class TestHotReloadableRegistry(unittest.TestCase):
    def test_strict_raises_on_invalid_schema(self) -> None:
        with self.assertRaises(ValueError):
            HotReloadableRegistry.from_path("/nonexistent/path/registry.json", strict=True)

    def test_load_then_current_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "model_dir_real"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])

            with mock.patch(
                "cta.live.hot_reloadable_registry.ClusterModelRegistry.from_registry_json"
            ) as patched_load:
                fake_registry = mock.MagicMock(name="fake_registry")
                patched_load.return_value = fake_registry
                wrapper = HotReloadableRegistry.from_path(registry_path)
                self.assertEqual(wrapper.version, 1)
                self.assertIs(wrapper.current, fake_registry)

    def test_reload_success_increments_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "model_dir_real"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])

            with mock.patch(
                "cta.live.hot_reloadable_registry.ClusterModelRegistry.from_registry_json"
            ) as patched_load:
                v1 = mock.MagicMock(name="v1")
                v2 = mock.MagicMock(name="v2")
                patched_load.side_effect = [v1, v2]
                wrapper = HotReloadableRegistry.from_path(registry_path)
                self.assertEqual(wrapper.version, 1)
                self.assertIs(wrapper.current, v1)
                ok, _ = wrapper.reload()
                self.assertTrue(ok)
                self.assertEqual(wrapper.version, 2)
                self.assertIs(wrapper.current, v2)

    def test_reload_invalid_schema_keeps_old_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "model_dir_real"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])

            with mock.patch(
                "cta.live.hot_reloadable_registry.ClusterModelRegistry.from_registry_json"
            ) as patched_load:
                v1 = mock.MagicMock(name="v1")
                patched_load.return_value = v1
                wrapper = HotReloadableRegistry.from_path(registry_path)
                self.assertEqual(wrapper.version, 1)
                # 破坏 registry：让 model_dir 不存在
                _write_registry(registry_path, [_make_entry(tmp / "missing_dir")])
                ok, report = wrapper.reload()
                self.assertFalse(ok)
                # 旧版本仍然保留
                self.assertEqual(wrapper.version, 1)
                self.assertIs(wrapper.current, v1)
                self.assertFalse(report.is_valid)

    def test_reload_load_exception_keeps_old_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "model_dir_real"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])

            with mock.patch(
                "cta.live.hot_reloadable_registry.ClusterModelRegistry.from_registry_json"
            ) as patched_load:
                v1 = mock.MagicMock(name="v1")
                # 第一次成功，第二次 raise
                patched_load.side_effect = [v1, RuntimeError("simulated load failure")]
                wrapper = HotReloadableRegistry.from_path(registry_path)
                self.assertEqual(wrapper.version, 1)
                # reload 触发异常
                ok, _ = wrapper.reload()
                self.assertFalse(ok)
                # 旧版本保留
                self.assertEqual(wrapper.version, 1)
                self.assertIs(wrapper.current, v1)


class TestSighupHandler(unittest.TestCase):
    def test_install_sighup_handler_binds_signal(self) -> None:
        import signal
        import threading
        if not hasattr(signal, "SIGHUP"):
            self.skipTest("SIGHUP not on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "m"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])

            with mock.patch(
                "cta.live.hot_reloadable_registry.ClusterModelRegistry.from_registry_json"
            ) as patched_load:
                patched_load.return_value = mock.MagicMock()
                wrapper = HotReloadableRegistry.from_path(registry_path)
                old_handler = signal.getsignal(signal.SIGHUP)
                try:
                    wrapper.install_sighup_handler()
                    new_handler = signal.getsignal(signal.SIGHUP)
                    # Handler 已经从 default 切到自定义
                    self.assertIsNot(new_handler, signal.SIG_DFL)
                    self.assertIsNot(new_handler, old_handler)
                finally:
                    signal.signal(signal.SIGHUP, old_handler)

    def test_install_sighup_in_non_main_thread_raises(self) -> None:
        import signal
        import threading
        if not hasattr(signal, "SIGHUP"):
            self.skipTest("SIGHUP not on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "m"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])

            with mock.patch(
                "cta.live.hot_reloadable_registry.ClusterModelRegistry.from_registry_json"
            ) as patched_load:
                patched_load.return_value = mock.MagicMock()
                wrapper = HotReloadableRegistry.from_path(registry_path)

                exc_holder: list[Exception] = []

                def attempt() -> None:
                    try:
                        wrapper.install_sighup_handler()
                    except Exception as e:  # noqa: BLE001
                        exc_holder.append(e)

                t = threading.Thread(target=attempt)
                t.start()
                t.join()
                self.assertEqual(len(exc_holder), 1)
                self.assertIsInstance(exc_holder[0], RuntimeError)

    def test_sighup_triggers_reload(self) -> None:
        """模拟 SIGHUP 调用 handler，应触发 reload + 版本 +1。"""
        import os
        import signal as sig_module
        import time
        if not hasattr(sig_module, "SIGHUP"):
            self.skipTest("SIGHUP not on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            model_dir = tmp / "m"
            model_dir.mkdir()
            registry_path = tmp / "registry.json"
            _write_registry(registry_path, [_make_entry(model_dir)])

            with mock.patch(
                "cta.live.hot_reloadable_registry.ClusterModelRegistry.from_registry_json"
            ) as patched_load:
                patched_load.return_value = mock.MagicMock()
                wrapper = HotReloadableRegistry.from_path(registry_path)
                self.assertEqual(wrapper.version, 1)

                old_handler = sig_module.getsignal(sig_module.SIGHUP)
                try:
                    wrapper.install_sighup_handler()
                    # 发 SIGHUP 给自己
                    os.kill(os.getpid(), sig_module.SIGHUP)
                    # 给 handler 一点时间执行
                    time.sleep(0.05)
                    # reload 应已触发 → version 升到 2
                    self.assertEqual(wrapper.version, 2)
                finally:
                    sig_module.signal(sig_module.SIGHUP, old_handler)


if __name__ == "__main__":
    unittest.main()
