"""cta.live.live_runner 单测。"""
from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.live.live_runner import LiveCtpSetting, LiveRunConfig, prepare_live_risk_wiring, run_live, serve_live
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.sim.sim_runner import SimnowSetting


class _DummyStrategyClass:
    pass


class TestLiveCtpSetting(unittest.TestCase):
    def test_to_vnpy_mapping(self) -> None:
        cfg = LiveCtpSetting(
            userid="u1",
            password="p1",
            brokerid="9999",
            td_address="tcp://1.2.3.4:5",
            md_address="tcp://6.7.8.9:10",
            auth_code="auth",
            appid="app",
            product_info="prod",
        )
        out = cfg.to_vnpy()
        self.assertEqual(out["用户名"], "u1")
        self.assertEqual(out["密码"], "p1")
        self.assertEqual(out["经纪商代码"], "9999")
        self.assertEqual(out["交易服务器"], "tcp://1.2.3.4:5")
        self.assertEqual(out["行情服务器"], "tcp://6.7.8.9:10")
        self.assertEqual(out["授权编码"], "auth")
        self.assertEqual(out["产品名称"], "app")
        self.assertEqual(out["产品信息"], "prod")


class TestRunLive(unittest.TestCase):
    def test_prepare_live_risk_wiring_injects_entry_gate_chain_from_same_oot_cfg(self) -> None:
        live_cfg = LiveRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb_live",
            vt_symbol="rb888.SHFE",
            setting={"oot_cfg": OotEvaluationConfig()},
        )

        prepared = prepare_live_risk_wiring(live_cfg)

        self.assertIs(prepared, live_cfg)
        self.assertIn("entry_gate_chain", prepared.setting)
        self.assertIn("position_evaluator", prepared.setting)

    def test_prepare_live_risk_wiring_injects_signal_generator_context_when_registry_given(self) -> None:
        with TemporaryDirectory(prefix="live_signal_ctx_") as td:
            root = Path(td)
            model_dir = root / "model_black_day"
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "provenance.json").write_text("{}", encoding="utf-8")
            registry_path = root / "cluster_registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "run_tag": "20260530",
                        "group_by": "cluster",
                        "trade_side_mode": "both",
                        "intervals": ["day"],
                        "entries": [
                            {
                                "interval": "day",
                                "group_name": "cluster_black",
                                "pool_name": "GRP_CLUSTER_BLACK",
                                "model_dir": str(model_dir),
                                "members": [{"symbol": "RB0", "exchange": "SHFE"}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manifest_path = root / "score_quantile_manifest_latest.json"
            manifest_path.write_text(json.dumps({"entries": []}), encoding="utf-8")
            live_cfg = LiveRunConfig(
                strategy_class=_DummyStrategyClass,
                strategy_name="rb_live",
                vt_symbol="rb888.SHFE",
                setting={
                    "oot_cfg": OotEvaluationConfig(),
                    "cluster_registry_path": str(registry_path),
                    "score_manifest_path": str(manifest_path),
                },
            )

            prepared = prepare_live_risk_wiring(live_cfg)

            self.assertIn("signal_generator_context", prepared.setting)
            ctx = prepared.setting["signal_generator_context"]
            self.assertIn("model_registry", ctx)
            self.assertIn("score_manifest", ctx)
            self.assertIn("generate_fn", ctx)

    def test_run_live_delegates_to_run_sim(self) -> None:
        live_cfg = LiveRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb_live",
            vt_symbol="rb888.SHFE",
            setting={"lookback": 20},
        )
        ctp = LiveCtpSetting(
            userid="u1",
            password="p1",
            brokerid="9999",
            td_address="tcp://td:1",
            md_address="tcp://md:2",
        )
        me = SimpleNamespace(name="main_engine")

        with patch("cta.live.live_runner.run_sim", return_value=me) as mock_run_sim:
            ret = run_live(
                live_cfg,
                ctp,
                gateway_name="CTP-LIVE",
                cta_engine_name="CtaStrategyLive",
                main_engine_factory=lambda: me,
            )

        self.assertIs(ret, me)
        mock_run_sim.assert_called_once()
        args, kwargs = mock_run_sim.call_args
        self.assertIs(args[0], live_cfg)
        self.assertIsInstance(args[1], SimnowSetting)
        sim_setting: SimnowSetting = args[1]
        self.assertEqual(sim_setting.userid, "u1")
        self.assertEqual(sim_setting.password, "p1")
        self.assertEqual(sim_setting.brokerid, "9999")
        self.assertEqual(sim_setting.td_address, "tcp://td:1")
        self.assertEqual(sim_setting.md_address, "tcp://md:2")
        self.assertEqual(kwargs["gateway_name"], "CTP-LIVE")
        self.assertEqual(kwargs["cta_engine_name"], "CtaStrategyLive")
        self.assertTrue(callable(kwargs["main_engine_factory"]))

    def test_run_live_blocks_when_preopen_checklist_fails(self) -> None:
        with TemporaryDirectory(prefix="live_preopen_fail_") as td:
            pred = Path(td) / "predictions.csv"
            pred.write_text("x", encoding="utf-8")
            live_cfg = LiveRunConfig(
                strategy_class=_DummyStrategyClass,
                strategy_name="rb_live",
                vt_symbol="rb888.SHFE",
                setting={
                    "lookback": 20,
                    "preopen_checklist": {
                        "predictions_path": str(pred),
                        "kill_switch_active": True,
                        "available_cash": 1_000_000.0,
                        "required_margin": 100_000.0,
                    },
                },
            )
            ctp = LiveCtpSetting(
                userid="u1",
                password="p1",
                brokerid="9999",
                td_address="tcp://td:1",
                md_address="tcp://md:2",
            )
            with self.assertRaises(RuntimeError):
                run_live(live_cfg, ctp, main_engine_factory=lambda: SimpleNamespace())

    def test_run_live_reconciliation_blocks_on_mismatch(self) -> None:
        live_cfg = LiveRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb_live",
            vt_symbol="rb888.SHFE",
            setting={"lookback": 20},
        )
        ctp = LiveCtpSetting(
            userid="u1",
            password="p1",
            brokerid="9999",
            td_address="tcp://td:1",
            md_address="tcp://md:2",
        )
        with TemporaryDirectory(prefix="live_reconcile_") as td:
            snap_path = Path(td) / "state_snapshot.json"
            state = PortfolioState(equity=1_000_000.0)
            state.add_position(
                {
                    "pos_id": "p1",
                    "symbol": "RB0",
                    "exchange": "SHFE",
                    "cluster": "black",
                    "direction": "long",
                    "notional": 100_000.0,
                }
            )
            snap_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
            live_cfg.state_snapshot_path = str(snap_path)
            live_cfg.enable_broker_reconciliation = True
            live_cfg.broker_positions_provider = lambda: []
            with self.assertRaises(RuntimeError):
                run_live(live_cfg, ctp, main_engine_factory=lambda: SimpleNamespace())

    def test_run_live_reconciliation_blocks_when_volume_mismatch_with_same_key(self) -> None:
        live_cfg = LiveRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb_live",
            vt_symbol="rb888.SHFE",
            setting={"lookback": 20},
        )
        ctp = LiveCtpSetting(
            userid="u1",
            password="p1",
            brokerid="9999",
            td_address="tcp://td:1",
            md_address="tcp://md:2",
        )
        with TemporaryDirectory(prefix="live_reconcile_vol_") as td:
            snap_path = Path(td) / "state_snapshot.json"
            state = PortfolioState(equity=1_000_000.0)
            state.add_position(
                {
                    "pos_id": "p1",
                    "symbol": "RB0",
                    "exchange": "SHFE",
                    "cluster": "black",
                    "direction": "long",
                    "notional": 100_000.0,
                    "volume": 1.0,
                }
            )
            snap_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
            live_cfg.state_snapshot_path = str(snap_path)
            live_cfg.enable_broker_reconciliation = True
            live_cfg.broker_positions_provider = lambda: [
                {"symbol": "RB0", "exchange": "SHFE", "direction": "long", "volume": 2.0}
            ]
            with self.assertRaises(RuntimeError):
                run_live(live_cfg, ctp, main_engine_factory=lambda: SimpleNamespace())

    def test_run_live_reconciliation_passes_when_volume_matches(self) -> None:
        live_cfg = LiveRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb_live",
            vt_symbol="rb888.SHFE",
            setting={"lookback": 20},
        )
        ctp = LiveCtpSetting(
            userid="u1",
            password="p1",
            brokerid="9999",
            td_address="tcp://td:1",
            md_address="tcp://md:2",
        )
        with TemporaryDirectory(prefix="live_reconcile_pass_") as td:
            snap_path = Path(td) / "state_snapshot.json"
            state = PortfolioState(equity=1_000_000.0)
            state.add_position(
                {
                    "pos_id": "p1",
                    "symbol": "RB0",
                    "exchange": "SHFE",
                    "cluster": "black",
                    "direction": "long",
                    "notional": 100_000.0,
                    "volume": 1.0,
                }
            )
            snap_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
            live_cfg.state_snapshot_path = str(snap_path)
            live_cfg.enable_broker_reconciliation = True
            live_cfg.broker_positions_provider = lambda: [
                {"symbol": "RB0", "exchange": "SHFE", "direction": "long", "volume": 1.0}
            ]
            with patch("cta.live.live_runner.run_sim", return_value=SimpleNamespace()) as mock_run_sim:
                ret = run_live(live_cfg, ctp, main_engine_factory=lambda: SimpleNamespace())
            self.assertIsNotNone(ret)
            mock_run_sim.assert_called_once()


class TestServeLive(unittest.TestCase):
    def test_enable_supervisor_runs_supervisor_loop(self) -> None:
        me = SimpleNamespace()
        stop = threading.Event()

        sup_instance = MagicMock()

        def _loop(ev: threading.Event) -> int:
            ev.wait(0.01)
            return 1

        sup_instance.loop.side_effect = _loop

        with (
            patch("cta.live.live_runner.Supervisor", return_value=sup_instance) as sup_cls,
            patch("cta.live.live_runner.serve_forever", return_value=None) as sf,
        ):
            serve_live(
                me,
                connect_setting={"用户名": "u1"},
                gateway_name="CTP",
                stop_event=stop,
                install_signal_handlers=False,
                enable_supervisor=True,
                supervisor_check_interval=1.0,
                supervisor_max_reconnects=7,
            )

        sup_cls.assert_called_once_with(
            me,
            gateway_name="CTP",
            connect_setting={"用户名": "u1"},
            check_interval=1.0,
            max_reconnects=7,
        )
        sf.assert_called_once()
        sup_instance.loop.assert_called_once()

    def test_disable_supervisor_skips_supervisor(self) -> None:
        me = SimpleNamespace()
        stop = threading.Event()
        with (
            patch("cta.live.live_runner.Supervisor") as sup_cls,
            patch("cta.live.live_runner.serve_forever", return_value=None) as sf,
        ):
            serve_live(
                me,
                connect_setting={"用户名": "u1"},
                gateway_name="CTP",
                stop_event=stop,
                install_signal_handlers=False,
                enable_supervisor=False,
            )
        sup_cls.assert_not_called()
        sf.assert_called_once()


if __name__ == "__main__":
    unittest.main()
