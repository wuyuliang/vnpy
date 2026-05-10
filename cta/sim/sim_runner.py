"""SimNow / 实盘 CTP 仿真运行器。

启动流程：
1. 实例化 ``EventEngine`` + ``MainEngine``
2. 注册 ``CtpGateway`` + ``CtaStrategyApp``
3. 用 ``SimnowSetting`` 翻译为 vnpy_ctp 的中文 key 字典并 ``connect``
4. 通过 CtaStrategy app 的 cta_engine 加策略 → init → start
5. 返回 main_engine（调用方负责 ``main_engine.close()`` 收尾）

测试通过 ``main_engine_factory`` 注入 fake，不真实连接 SimNow；
真实运行需要 ``vnpy_ctp`` 已安装、SimNow 账号有效、网络可达。

SimNow 公开仿真服务器（2026-05 数据，可能变化）：
- 7x24 仿真：
    交易: tcp://180.168.146.187:10130   行情: tcp://180.168.146.187:10131
- 实盘交易日仿真：
    交易: tcp://180.168.146.187:10101   行情: tcp://180.168.146.187:10111
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


SIMNOW_DEFAULT: dict[str, str] = {
    "brokerid": "9999",
    "auth_code": "0000000000000000",
    "appid": "simnow_client_test",
    "product_info": "",
    "td_address": "tcp://180.168.146.187:10130",
    "md_address": "tcp://180.168.146.187:10131",
}


@dataclass
class SimnowSetting:
    """SimNow / CTP 仿真账号配置。字段命名遵循 vnpy_ctp 中文 key 的语义对应。"""

    userid: str
    password: str
    brokerid: str = SIMNOW_DEFAULT["brokerid"]
    auth_code: str = SIMNOW_DEFAULT["auth_code"]
    appid: str = SIMNOW_DEFAULT["appid"]
    product_info: str = SIMNOW_DEFAULT["product_info"]
    td_address: str = SIMNOW_DEFAULT["td_address"]
    md_address: str = SIMNOW_DEFAULT["md_address"]

    def to_vnpy(self) -> dict[str, str]:
        """vnpy_ctp 接受的中文 key 配置字典。"""
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


@dataclass
class SimRunConfig:
    """单策略仿真启动配置。"""

    strategy_class: type
    strategy_name: str
    vt_symbol: str
    setting: dict
    # —— 历史预热（流式策略需要前置 K 线把特征算稳）——
    warmup_days: int = 0
    warmup_interval: str = "1m"   # vnpy Interval value: "1m" / "1h" / "d"
    # —— 风控集成 ——
    risk_guard: Any = None        # cta.live.risk.RiskGuard | None
    kill_switch: Any = None       # cta.live.kill_switch.KillSwitch | None
    capital: float = 1_000_000.0
    contract_size_resolver: Callable[[str], float] | None = None
    commission_resolver: Callable[[str, float, float], float] | None = None
    # —— 成交流水落盘 ——
    trade_recorder_dir: str | None = None
    # —— 关闭 PnL tracker（风控需要时即便用户没显式指定，run_sim 也会挂）——
    enable_pnl_tracker: bool = True


def _default_main_engine_factory():  # pragma: no cover - 实际运行才走到
    """生产环境：装好 vnpy_ctp 时构造 MainEngine + CtpGateway + CtaStrategyApp。"""
    from vnpy.event import EventEngine
    from vnpy.trader.engine import MainEngine

    try:
        from vnpy_ctp import CtpGateway  # type: ignore
    except ImportError as e:
        raise ImportError(
            "vnpy_ctp 未安装；pip install vnpy_ctp 后重试，或为测试场景注入 main_engine_factory。"
        ) from e

    from vnpy_ctastrategy import CtaStrategyApp  # type: ignore

    ee = EventEngine()
    me = MainEngine(ee)
    me.add_gateway(CtpGateway)
    me.add_app(CtaStrategyApp)
    return me


def run_sim(
    cfg: SimRunConfig,
    simnow: SimnowSetting,
    *,
    gateway_name: str = "CTP",
    cta_engine_name: str = "CtaStrategy",
    main_engine_factory: Callable[[], Any] | None = None,
):
    """启动一个 CtaStrategy 仿真实例。

    Parameters
    ----------
    cfg
        策略类、策略名、合约 vt_symbol、setting 字典。
    simnow
        SimNow / CTP 仿真账号 + 服务器配置。
    main_engine_factory
        测试可注入 fake；生产留 ``None`` 使用默认实现（需要 vnpy_ctp）。

    Returns
    -------
    main_engine
        已启动的 MainEngine。调用方负责 ``main_engine.close()``。
    """
    factory = main_engine_factory or _default_main_engine_factory
    me = factory()
    setting = simnow.to_vnpy()
    logger.info(
        "connecting %s gateway: td=%s md=%s",
        gateway_name, simnow.td_address, simnow.md_address,
    )
    me.connect(setting, gateway_name)

    cta_engine = me.get_engine(cta_engine_name)
    if cta_engine is None:
        raise RuntimeError(f"main_engine.get_engine({cta_engine_name!r}) returned None")

    if hasattr(cta_engine, "init_engine"):
        cta_engine.init_engine()  # 加载本地策略 / 持仓 / 数据；fake 中可空实现

    # vnpy_ctastrategy.CtaEngine.add_strategy 第一参数是**字符串类名**，从 self.classes
    # 查类。先把策略类注册到 classes 字典，再用字符串名调用，与真实环境一致。
    class_name = cfg.strategy_class.__name__
    classes_dict = getattr(cta_engine, "classes", None)
    if isinstance(classes_dict, dict):
        classes_dict[class_name] = cfg.strategy_class
        cta_engine.add_strategy(class_name, cfg.strategy_name, cfg.vt_symbol, dict(cfg.setting or {}))
    else:
        # cta_engine 没有 classes 字典：按 class object 调用（兼容简单 fake）
        cta_engine.add_strategy(cfg.strategy_class, cfg.strategy_name, cfg.vt_symbol, dict(cfg.setting or {}))

    # 取出 strategy 实例（vnpy 真实实现把它放在 cta_engine.strategies dict 中）
    strategies_attr = getattr(cta_engine, "strategies", None)
    strategy = None
    if isinstance(strategies_attr, dict):
        strategy = strategies_attr.get(cfg.strategy_name)
    if strategy is not None:
        _attach_observers(strategy, cfg)

    cta_engine.init_strategy(cfg.strategy_name)
    if strategy is not None and cfg.warmup_days > 0:
        _warmup_strategy(strategy, cfg)
    cta_engine.start_strategy(cfg.strategy_name)
    logger.info("strategy %s started on %s", cfg.strategy_name, cfg.vt_symbol)
    return me


def _attach_observers(strategy: Any, cfg: SimRunConfig) -> None:
    """把 trade_recorder / pnl_tracker / order_filter 挂到 strategy。
    对 LegacyCtaAdapter 子类按字段写入；对其他 CtaTemplate 子类做属性注入。"""
    from cta.live.kill_switch import KillSwitchRule
    from cta.live.pnl_tracker import DailyPnlTracker
    from cta.live.risk import RiskGuard, make_risk_filter
    from cta.live.trade_recorder import TradeRecorder

    if cfg.trade_recorder_dir:
        strategy.trade_recorder = TradeRecorder(
            out_dir=cfg.trade_recorder_dir,
            vt_symbol=cfg.vt_symbol,
        )

    needs_pnl = cfg.enable_pnl_tracker or (cfg.risk_guard is not None and cfg.kill_switch is None)
    if needs_pnl or cfg.risk_guard is not None:
        strategy.pnl_tracker = DailyPnlTracker(
            contract_size_resolver=cfg.contract_size_resolver,
            commission_resolver=cfg.commission_resolver,
        )

    if cfg.risk_guard is not None or cfg.kill_switch is not None:
        rules = list((cfg.risk_guard.rules if cfg.risk_guard else []) or [])
        if cfg.kill_switch is not None:
            rules.append(KillSwitchRule(cfg.kill_switch))
        merged = RiskGuard(rules=rules)
        provider = strategy.pnl_tracker.get_pnl if getattr(strategy, "pnl_tracker", None) else None
        strategy.order_filter = make_risk_filter(
            merged,
            capital=cfg.capital,
            daily_pnl_provider=provider,
        )


def _warmup_strategy(strategy: Any, cfg: SimRunConfig) -> None:
    """调用 vnpy CtaTemplate.load_bar 预加载历史 K 线。

    interval 字符串映射到 vnpy.trader.constant.Interval；找不到就直接传字符串。"""
    interval: Any = cfg.warmup_interval
    try:
        from vnpy.trader.constant import Interval  # type: ignore
        mapping = {
            "1m": Interval.MINUTE, "minute": Interval.MINUTE,
            "1h": Interval.HOUR, "hour": Interval.HOUR,
            "d": Interval.DAILY, "day": Interval.DAILY, "daily": Interval.DAILY,
        }
        interval = mapping.get(str(cfg.warmup_interval).lower(), interval)
    except ImportError:  # pragma: no cover
        pass
    try:
        strategy.load_bar(int(cfg.warmup_days), interval=interval)
    except TypeError:
        # fake 实现可能签名不同
        strategy.load_bar(int(cfg.warmup_days), interval)
    except Exception as e:  # noqa: BLE001
        logger.warning("warmup load_bar failed: %s", e)


def serve_forever(
    main_engine: Any,
    *,
    stop_event: Any | None = None,
    install_signal_handlers: bool = True,
    on_stop: Callable[[], None] | None = None,
) -> None:
    """阻塞主线程直到收到 SIGINT/SIGTERM（或外部 stop_event 被 set），然后关闭
    ``main_engine``。生产化的 sim / 实盘进程应在 ``run_sim(...)`` 之后调它来保活。

    Parameters
    ----------
    main_engine
        ``run_sim`` 返回值。
    stop_event
        ``threading.Event``；若不传内部自建一个。测试时可显式注入并预 ``set()``。
    install_signal_handlers
        生产环境 True，单测设 False 避免污染 pytest 的信号注册。
    on_stop
        可选 hook：在 ``main_engine.close()`` **之前**调用，用于持久化数据 / 发送告警。
    """
    import signal as _signal
    import threading

    stop = stop_event if stop_event is not None else threading.Event()
    if install_signal_handlers:
        def _handle(signum, frame):  # noqa: ARG001
            stop.set()
        try:
            _signal.signal(_signal.SIGINT, _handle)
            _signal.signal(_signal.SIGTERM, _handle)
        except (ValueError, OSError):  # pragma: no cover - 非主线程注册失败
            pass

    stop.wait()

    if on_stop is not None:
        try:
            on_stop()
        except Exception as e:  # noqa: BLE001
            logger.exception("on_stop hook error: %s", e)

    try:
        main_engine.close()
    except Exception as e:  # noqa: BLE001
        logger.exception("main_engine.close error: %s", e)


__all__ = ["SIMNOW_DEFAULT", "SimnowSetting", "SimRunConfig", "run_sim", "serve_forever"]
