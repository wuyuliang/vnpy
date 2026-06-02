"""实盘运行器：在 ``sim_runner`` 能力上提供 live 语义封装。

目标
----
1. 复用 ``cta.sim.sim_runner`` 的成熟启动链（connect → add_strategy → init/start）
2. 提供独立 ``LiveCtpSetting``（不绑定 SimNow 默认地址）
3. 提供 ``serve_live``：可选挂 ``Supervisor`` 自动重连，并阻塞主线程保活
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.live.supervisor import Supervisor
from cta.sim.sim_runner import (
    SimRunConfig,
    SimnowSetting,
    run_sim,
    serve_forever,
)

logger = logging.getLogger(__name__)


def _load_state_snapshot(path: str | None) -> PortfolioState | None:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to load state snapshot: {p}") from exc
    return PortfolioState.from_dict(payload)


def _position_key(symbol: object, exchange: object, direction: object) -> tuple[str, str, str]:
    return (str(symbol).upper(), str(exchange).upper(), str(direction).lower())


def _extract_position_volume(row: dict[str, Any]) -> float:
    """Best-effort lots/volume extraction with sensible fallback."""
    raw = row.get("volume", row.get("lots", row.get("qty", 1.0)))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return abs(v) if v != 0.0 else 0.0


def _reconcile_or_raise(
    state: PortfolioState | None,
    broker_positions: list[dict[str, Any]],
) -> None:
    if state is None:
        return
    broker_lots: dict[tuple[str, str, str], float] = {}
    for row in broker_positions:
        key = _position_key(row.get("symbol", ""), row.get("exchange", ""), row.get("direction", row.get("side", "")))
        broker_lots[key] = broker_lots.get(key, 0.0) + _extract_position_volume(row)
    local_lots: dict[tuple[str, str, str], float] = {}
    for pos in state.positions.values():
        key = _position_key(pos.get("symbol", ""), pos.get("exchange", ""), pos.get("direction", pos.get("side", "")))
        local_lots[key] = local_lots.get(key, 0.0) + _extract_position_volume(pos)
    all_keys = sorted(set(broker_lots) | set(local_lots))
    tol = 1e-9
    mismatch = {
        key: {
            "broker": float(broker_lots.get(key, 0.0)),
            "local": float(local_lots.get(key, 0.0)),
        }
        for key in all_keys
        if abs(float(broker_lots.get(key, 0.0)) - float(local_lots.get(key, 0.0))) > tol
    }
    if mismatch:
        raise RuntimeError(
            "broker reconciliation failed: broker lots != state snapshot lots. "
            f"diff={mismatch}"
        )


@dataclass
class LiveCtpSetting:
    """实盘 CTP 连接参数。"""

    userid: str
    password: str
    brokerid: str
    td_address: str
    md_address: str
    auth_code: str = ""
    appid: str = ""
    product_info: str = ""

    def to_vnpy(self) -> dict[str, str]:
        """转换为 vnpy_ctp 所需中文 key 字典。"""
        return {
            "用户名": self.userid,
            "密码": self.password,
            "经纪商代码": self.brokerid,
            "交易服务器": self.td_address,
            "行情服务器": self.md_address,
            "产品名称": self.appid,
            "授权编码": self.auth_code,
            "产品信息": self.product_info,
        }

    def to_simnow_setting(self) -> SimnowSetting:
        """桥接到 ``sim_runner.run_sim`` 复用启动链。"""
        return SimnowSetting(
            userid=self.userid,
            password=self.password,
            brokerid=self.brokerid,
            auth_code=self.auth_code,
            appid=self.appid,
            product_info=self.product_info,
            td_address=self.td_address,
            md_address=self.md_address,
        )


# 直接复用 sim_runner 的运行配置结构，避免双份参数定义漂移。
LiveRunConfig = SimRunConfig


def prepare_live_risk_wiring(cfg: LiveRunConfig) -> LiveRunConfig:
    """Inject sim/live gate adapters from the same OOT config when available."""
    setting = dict(cfg.setting or {})
    oot_cfg = setting.get("oot_cfg")
    if oot_cfg is None:
        cfg.setting = setting
        return cfg
    try:
        from cta.config.model_oot_eval_config import OotEvaluationConfig
        from cta.sim.adapters.entry_gate_chain import EntryGateChain
        from cta.sim.adapters.position_evaluator import PositionEvaluator
    except Exception as exc:  # noqa: BLE001
        logger.warning("live risk wiring imports failed: %s", exc)
        cfg.setting = setting
        return cfg
    if not isinstance(oot_cfg, OotEvaluationConfig):
        cfg.setting = setting
        return cfg
    setting.setdefault("entry_gate_chain", EntryGateChain(oot_cfg))
    setting.setdefault("position_evaluator", PositionEvaluator(oot_cfg=oot_cfg))
    registry_path = str(setting.get("cluster_registry_path", "") or "").strip()
    if registry_path and "signal_generator_context" not in setting:
        try:
            from cta.live.model_registry import LiveModelRegistry
            from cta.live.online_feature import OnlineFeatureLoader
            from cta.live.signal_generator import generate_today_candidates
            from cta.risk.state.score_quantile_manifest import ScoreQuantileManifest

            model_registry = LiveModelRegistry.from_registry_path(Path(registry_path), strict=True)
            manifest_path = Path(
                str(
                    setting.get(
                        "score_manifest_path",
                        "cta/model/manifests/score_quantile_manifest_latest.json",
                    )
                )
            )
            score_manifest = ScoreQuantileManifest.from_json(manifest_path)
            feature_loader = OnlineFeatureLoader(
                feature_root=str(setting.get("feature_root", "cta/data/feature"))
            )
            setting["signal_generator_context"] = {
                "cfg": oot_cfg,
                "model_registry": model_registry,
                "score_manifest": score_manifest,
                "feature_loader": feature_loader,
                "generate_fn": generate_today_candidates,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("live signal generator wiring skipped: %s", exc)
    cfg.setting = setting
    return cfg


def run_live(
    cfg: LiveRunConfig,
    ctp: LiveCtpSetting,
    *,
    gateway_name: str = "CTP",
    cta_engine_name: str = "CtaStrategy",
    main_engine_factory: Callable[[], Any] | None = None,
):
    """启动单策略 live 实例，返回已连接并已 start 的 ``main_engine``。"""
    cfg = prepare_live_risk_wiring(cfg)
    _run_preopen_checklist_or_raise(dict(cfg.setting or {}))
    logger.info(
        "starting live strategy=%s symbol=%s gateway=%s td=%s md=%s",
        cfg.strategy_name,
        cfg.vt_symbol,
        gateway_name,
        ctp.td_address,
        ctp.md_address,
    )
    if bool(getattr(cfg, "enable_broker_reconciliation", False)):
        provider = getattr(cfg, "broker_positions_provider", None)
        if provider is None:
            raise RuntimeError("enable_broker_reconciliation=True but broker_positions_provider is None")
        state = _load_state_snapshot(getattr(cfg, "state_snapshot_path", None))
        broker_positions = list(provider())
        _reconcile_or_raise(state, broker_positions)

    return run_sim(
        cfg,
        ctp.to_simnow_setting(),
        gateway_name=gateway_name,
        cta_engine_name=cta_engine_name,
        main_engine_factory=main_engine_factory,
    )


def _run_preopen_checklist_or_raise(setting: dict[str, Any]) -> None:
    raw = setting.get("preopen_checklist")
    if not isinstance(raw, dict):
        return
    from cta.live.preopen_checklist import PreopenCheckConfig, run_preopen_checklist

    pred_path = str(raw.get("predictions_path", "")).strip()
    if not pred_path:
        raise RuntimeError("preopen_checklist enabled but predictions_path is empty")
    signal_ctx = setting.get("signal_generator_context")
    model_registry = signal_ctx.get("model_registry") if isinstance(signal_ctx, dict) else None
    cfg = PreopenCheckConfig(
        max_prediction_stale_hours=float(raw.get("max_prediction_stale_hours", 24.0)),
        max_model_age_days=float(raw.get("max_model_age_days", 14.0)),
        margin_buffer_pct=float(raw.get("margin_buffer_pct", 0.0)),
    )
    report = run_preopen_checklist(
        predictions_path=Path(pred_path),
        model_registry=model_registry,
        kill_switch_active=bool(raw.get("kill_switch_active", False)),
        available_cash=float(raw.get("available_cash", 0.0)),
        required_margin=float(raw.get("required_margin", 0.0)),
        cfg=cfg,
    )
    if report.passed:
        return
    details = "; ".join(f"{item.name}:{item.detail}" for item in report.items if not item.passed)
    raise RuntimeError(f"preopen checklist failed: {details}")


def serve_live(
    main_engine: Any,
    *,
    connect_setting: dict[str, str],
    gateway_name: str = "CTP",
    stop_event: Any | None = None,
    install_signal_handlers: bool = True,
    on_stop: Callable[[], None] | None = None,
    enable_supervisor: bool = True,
    supervisor_check_interval: float = 10.0,
    supervisor_max_reconnects: int = 100,
) -> None:
    """可选启动 ``Supervisor``，并阻塞主线程直到停止信号。"""
    stop = stop_event or threading.Event()
    sup_stop = threading.Event()
    sup_thread: threading.Thread | None = None

    if enable_supervisor:
        sup = Supervisor(
            main_engine,
            gateway_name=gateway_name,
            connect_setting=dict(connect_setting),
            check_interval=float(supervisor_check_interval),
            max_reconnects=int(supervisor_max_reconnects),
        )
        sup_thread = threading.Thread(
            target=sup.loop,
            args=(sup_stop,),
            name=f"live-supervisor-{gateway_name}",
            daemon=True,
        )
        sup_thread.start()
        logger.info(
            "live supervisor started: gateway=%s check_interval=%.2fs max_reconnects=%d",
            gateway_name,
            float(supervisor_check_interval),
            int(supervisor_max_reconnects),
        )

    try:
        serve_forever(
            main_engine,
            stop_event=stop,
            install_signal_handlers=install_signal_handlers,
            on_stop=on_stop,
        )
    finally:
        if enable_supervisor:
            sup_stop.set()
            if sup_thread is not None:
                sup_thread.join(timeout=2.0)
            logger.info("live supervisor stopped: gateway=%s", gateway_name)


__all__ = [
    "LiveCtpSetting",
    "LiveRunConfig",
    "prepare_live_risk_wiring",
    "run_live",
    "serve_live",
]
