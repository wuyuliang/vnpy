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


def _reconcile_or_raise(
    state: PortfolioState | None,
    broker_positions: list[dict[str, Any]],
) -> None:
    if state is None:
        return
    broker_counts: dict[tuple[str, str, str], int] = {}
    for row in broker_positions:
        key = _position_key(row.get("symbol", ""), row.get("exchange", ""), row.get("direction", row.get("side", "")))
        broker_counts[key] = broker_counts.get(key, 0) + 1
    local_counts: dict[tuple[str, str, str], int] = {}
    for pos in state.positions.values():
        key = _position_key(pos.get("symbol", ""), pos.get("exchange", ""), pos.get("direction", pos.get("side", "")))
        local_counts[key] = local_counts.get(key, 0) + 1
    if broker_counts != local_counts:
        raise RuntimeError(
            "broker reconciliation failed: broker_positions != state_snapshot positions. "
            f"broker={broker_counts}, local={local_counts}"
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


def run_live(
    cfg: LiveRunConfig,
    ctp: LiveCtpSetting,
    *,
    gateway_name: str = "CTP",
    cta_engine_name: str = "CtaStrategy",
    main_engine_factory: Callable[[], Any] | None = None,
):
    """启动单策略 live 实例，返回已连接并已 start 的 ``main_engine``。"""
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
    "run_live",
    "serve_live",
]
