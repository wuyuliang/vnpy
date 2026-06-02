"""LegacyCtaAdapter — 把现有 ``on_bar(i, bar, position)`` 接口的 v1 策略
适配到 ``vnpy_ctastrategy.CtaTemplate``，使得同一份核心策略逻辑可在：

- ``cta.skills.data_backtest.event_driven_backtest`` （研究用，单进程极快）
- ``vnpy_ctabacktester.BacktestingEngine`` （准实盘对齐回测）
- ``vnpy_ctp`` SimNow / 实盘
- 三处复用，避免逻辑分叉造成的回测↔实盘背离。

子类约定
--------
1. ``parameters: list[str]`` — vnpy ``setting`` 中允许覆盖的字段名。
2. ``prepare_frame(df) -> pd.DataFrame`` — 把 OHLCV 预处理为含特征的 DataFrame
   （和 v1 ``prepare_strategy_frame`` 等价的逻辑）。
3. ``make_inner(frame) -> object`` — 实例化 v1 策略（提供 ``on_bar(i, bar, pos)`` 接口）。
   返回对象的 ``frame`` 属性需可写（adapter 在 stream 模式下会更新它）。

Adapter 行为
------------
- 在 ``on_bar`` 中累积一个限长 buffer（默认 1000 根 bar）。
- 每根 bar 调用 ``prepare_frame`` 重建特征，再调用 ``inner.on_bar(i, last, pos)``。
- 把 v1 订单字典翻译成 ``buy/sell/short/cover``（``order_type='stop'`` → ``stop=True``）。
- 任何来自 ``prepare_frame`` / ``make_inner`` / ``inner.on_bar`` 的异常被捕获并写日志，
  不向上传播，避免 cta_engine 崩溃。

风控接入
--------
通过 ``order_filter`` 类属性 / 实例属性注入 pre-trade hook：

    adapter.order_filter = make_risk_filter(RiskGuard([...]))

签名：``Callable[[order_dict, adapter], bool]``。返回 ``False`` 时 adapter 跳过该订单
（不调 buy/sell/short/cover），用于风控、kill switch、合规过滤等场景。

不做的事
--------
- 不做多品种持仓拆分（CtaTemplate 单 vt_symbol 模型）。
- 不调用 ``cancel_all`` 之外的撤单（v1 接口没有撤单语义）。
"""
from __future__ import annotations

import math
from typing import Any, Callable

import pandas as pd

from vnpy.trader.object import BarData
from vnpy_ctastrategy import CtaTemplate


def bars_to_df(bars: list[BarData]) -> pd.DataFrame:
    """Convert a list of vnpy BarData into a pandas DataFrame compatible with
    v1 strategies (columns datetime/open/high/low/close/volume/open_interest/turnover).
    """
    if not bars:
        return pd.DataFrame(
            columns=["datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"]
        )
    return pd.DataFrame(
        {
            "datetime": [b.datetime for b in bars],
            "open": [float(b.open_price) for b in bars],
            "high": [float(b.high_price) for b in bars],
            "low": [float(b.low_price) for b in bars],
            "close": [float(b.close_price) for b in bars],
            "volume": [float(b.volume) for b in bars],
            "open_interest": [float(b.open_interest) for b in bars],
            "turnover": [float(b.turnover) for b in bars],
        }
    )


class LegacyCtaAdapter(CtaTemplate):
    """Base CtaTemplate that wraps a v1 ``on_bar(i, bar, position)`` strategy."""

    parameters: list[str] = []
    variables: list[str] = []
    history_size: int = 1000
    # Pre-trade hook: ``Callable[[order_dict, adapter], bool]``，返回 False 时跳过 send_order
    order_filter: Callable[[dict, "LegacyCtaAdapter"], bool] | None = None
    # 可选拆单器：``Callable[[parent_lots, order_dict, adapter], list[int]]``。
    order_slicer: Callable[[int, dict, "LegacyCtaAdapter"], list[int]] | None = None
    # 实盘 / 仿真观测点（M3）：成交流水记录器与日内已实现 PnL 跟踪器
    trade_recorder: Any = None  # cta.live.trade_recorder.TradeRecorder | None
    pnl_tracker: Any = None     # cta.live.pnl_tracker.DailyPnlTracker | None
    oot_trade_logger: Any = None  # cta.live.trade_logger.OotStyleTradeLogger | None
    entry_gate_chain: Any = None
    position_evaluator: Any = None
    state_provider: Any = None
    signal_generator_context: Any = None

    def __init__(self, cta_engine: Any, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self._buffer: list[BarData] = []
        self._inner: Any = None
        self._frame: pd.DataFrame | None = None
        st = dict(setting or {})
        self.entry_gate_chain = st.get("entry_gate_chain")
        self.position_evaluator = st.get("position_evaluator")
        self.state_provider = st.get("state_provider")
        self.signal_generator_context = st.get("signal_generator_context")
        self._open_position_ctx: dict[str, Any] | None = None

    # ------- subclass hooks -------
    def prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def make_inner(self, frame: pd.DataFrame) -> Any:
        raise NotImplementedError

    # ------- CtaTemplate callbacks -------
    def on_init(self) -> None:
        self._buffer = []
        self._inner = None
        self._frame = None
        self.write_log(f"{self.__class__.__name__} init")

    def on_start(self) -> None:
        self.write_log(f"{self.__class__.__name__} start")

    def on_stop(self) -> None:
        self.write_log(f"{self.__class__.__name__} stop")
        self.cancel_all()
        if self.trade_recorder is not None:
            try:
                path = self.trade_recorder.flush()
                if path:
                    self.write_log(f"trade_recorder flushed: {path}")
            except Exception as e:  # noqa: BLE001
                self.write_log(f"trade_recorder flush error: {e}")
        if self.oot_trade_logger is not None:
            try:
                paths = self.oot_trade_logger.flush()
                self.write_log(f"oot_trade_logger flushed: {paths}")
            except Exception as e:  # noqa: BLE001
                self.write_log(f"oot_trade_logger flush error: {e}")

    def on_trade(self, trade: Any) -> None:
        """实盘 / 仿真成交回报：转给 trade_recorder 与 pnl_tracker。
        v1 策略的状态由 on_bar 驱动，此处不再调 inner。"""
        if self.trade_recorder is not None:
            try:
                self.trade_recorder.record(trade)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"trade_recorder error: {e}")
        if self.oot_trade_logger is not None:
            try:
                trade_side = self._normalize_direction(
                    str(getattr(getattr(trade, "direction", None), "value", "")).strip()
                )
                self.oot_trade_logger.record_fill(
                    {
                        "datetime": getattr(trade, "datetime", ""),
                        "symbol": getattr(trade, "symbol", ""),
                        "exchange": getattr(getattr(trade, "exchange", None), "value", ""),
                        "interval": getattr(self, "interval", ""),
                        "signal_type": "",
                        "side": trade_side,
                        "execution_status": "filled",
                        "entry_fill_price": float(getattr(trade, "price", 0.0) or 0.0),
                        "entry_lots": int(float(getattr(trade, "volume", 0.0) or 0.0)),
                    }
                )
            except Exception as e:  # noqa: BLE001
                self.write_log(f"oot_trade_logger error: {e}")
        if self.pnl_tracker is not None:
            try:
                self.pnl_tracker.on_trade(trade)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"pnl_tracker error: {e}")
        self._update_position_context_from_trade(trade)

    def on_bar(self, bar: BarData) -> None:
        self._buffer.append(bar)
        if len(self._buffer) > self.history_size:
            del self._buffer[: len(self._buffer) - self.history_size]
        if self._maybe_apply_position_evaluator(bar):
            return
        if len(self._buffer) < 2:
            return

        df = bars_to_df(self._buffer)
        try:
            df = self.prepare_frame(df)
        except Exception as e:  # noqa: BLE001
            self.write_log(f"prepare_frame failed: {e}")
            return
        self._frame = df

        if self._inner is None:
            try:
                self._inner = self.make_inner(df)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"make_inner failed: {e}")
                return
        else:
            # 把最新 frame 注入 inner（约定 inner.frame 可写）
            try:
                self._inner.frame = df
            except AttributeError:
                pass

        i = len(df) - 1
        try:
            orders = list(self._inner.on_bar(i, df.iloc[i], int(self.pos)))
        except Exception as e:  # noqa: BLE001
            self.write_log(f"inner.on_bar error: {e}")
            return

        for o in orders:
            self._dispatch_order(o, bar)

    # ------- order translation -------
    def _dispatch_order(self, order: dict, bar: BarData) -> None:
        order = dict(order)
        side = str(order.get("side", "")).lower()
        bypass_filters = bool(order.get("bypass_filters", False))
        lots = int(order.get("lots", 0))
        if lots <= 0:
            return
        order_type = str(order.get("order_type", "market")).lower()
        stop = order_type == "stop"
        price = float(order.get("price", bar.close_price))
        symbol = str(self.vt_symbol).split(".", 1)[0]
        exchange = str(self.vt_symbol).split(".", 1)[1] if "." in str(self.vt_symbol) else ""
        if side in {"long", "short"}:
            order = self._enrich_order_from_signal_runtime(order, bar, symbol=symbol, exchange=exchange)
        lots = int(order.get("lots", lots))
        payload = {
            "datetime": getattr(bar, "datetime", ""),
            "symbol": symbol,
            "exchange": exchange,
            "interval": getattr(self, "interval", ""),
            "signal_type": str(order.get("signal_type", "")),
            "side": side,
            "entry_fill_price": price,
            "entry_lots": lots,
            "trade_filter_prob": order.get("trade_filter_prob"),
            "trade_filter_prob_pctl": order.get("trade_filter_prob_pctl"),
            "final_decision_score": order.get("final_decision_score"),
        }

        if side in {"long", "short"} and not bypass_filters and self.entry_gate_chain is not None:
            decision = self._evaluate_entry_gate_chain(order, bar, symbol=symbol, exchange=exchange, lots=lots)
            if decision is None or not bool(getattr(decision, "passed", False)):
                block_reason = "blocked_entry_gate"
                risk_block = ""
                if decision is not None:
                    block_reason = str(getattr(decision, "block_reason", "") or "blocked_entry_gate")
                    stage = str(getattr(decision, "block_stage", "") or "")
                    if stage.startswith("risk_"):
                        risk_block = block_reason
                self._record_blocked(payload, block_reason=block_reason, risk_block_reason=risk_block)
                return
            lots = max(0, int(getattr(decision, "adjusted_lots", lots)))
            if lots <= 0:
                self._record_blocked(payload, block_reason="blocked_zero_lots", risk_block_reason="blocked_zero_lots")
                return
            order["lots"] = lots
            payload["entry_lots"] = lots

        if self.order_filter is not None and not bypass_filters:
            try:
                allowed = bool(self.order_filter(dict(order), self))
            except Exception as e:  # noqa: BLE001
                self.write_log(f"order_filter error: {e}")
                allowed = False
            if not allowed:
                self._record_blocked(
                    payload,
                    block_reason=str(order.get("block_reason", "blocked_order_filter")),
                    risk_block_reason=str(order.get("risk_block_reason", "")),
                )
                return

        if self.oot_trade_logger is not None:
            try:
                self.oot_trade_logger.record_decision(payload, passed=True, adjusted_lots=lots)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"oot_trade_logger error: {e}")

        slice_lots = [lots]
        if self.order_slicer is not None and side in {"long", "short"}:
            try:
                raw = list(self.order_slicer(int(lots), dict(order), self))
                valid = [int(x) for x in raw if int(x) > 0]
                if valid:
                    slice_lots = valid
            except Exception as e:  # noqa: BLE001
                self.write_log(f"order_slicer error: {e}")

        if side == "long":
            self._open_position_ctx = {
                "side": "long",
                "entry_price": float(price),
                "symbol": symbol,
                "exchange": exchange,
                "interval": str(getattr(self, "interval", "")),
            }
            for child_lots in slice_lots:
                self.buy(price, int(child_lots), stop=stop)
            return
        if side == "short":
            self._open_position_ctx = {
                "side": "short",
                "entry_price": float(price),
                "symbol": symbol,
                "exchange": exchange,
                "interval": str(getattr(self, "interval", "")),
            }
            for child_lots in slice_lots:
                self.short(price, int(child_lots), stop=stop)
            return
        if side == "flat":
            pos = int(self.pos)
            if pos > 0:
                self.sell(price, abs(pos), stop=stop)
            elif pos < 0:
                self.cover(price, abs(pos), stop=stop)
            self._open_position_ctx = None

    def _record_blocked(self, payload: dict[str, Any], *, block_reason: str, risk_block_reason: str = "") -> None:
        if self.oot_trade_logger is None:
            return
        try:
            self.oot_trade_logger.record_decision(
                payload,
                passed=False,
                block_reason=str(block_reason),
                risk_block_reason=str(risk_block_reason or block_reason),
                adjusted_lots=0,
            )
        except Exception as e:  # noqa: BLE001
            self.write_log(f"oot_trade_logger error: {e}")

    def _evaluate_entry_gate_chain(
        self,
        order: dict[str, Any],
        bar: BarData,
        *,
        symbol: str,
        exchange: str,
        lots: int,
    ) -> Any | None:
        if self.entry_gate_chain is None:
            return None
        candidate = dict(order)
        candidate.setdefault("symbol", symbol)
        candidate.setdefault("exchange", exchange)
        candidate.setdefault("interval", str(getattr(self, "interval", "")))
        candidate.setdefault("signal_type", str(order.get("signal_type", "")))
        candidate.setdefault("side", str(order.get("side", "")).lower())
        candidate["trade_filter_prob"] = order.get("trade_filter_prob")
        candidate["trade_filter_prob_pctl"] = order.get("trade_filter_prob_pctl")
        candidate["final_decision_score"] = order.get("final_decision_score")
        try:
            return self.entry_gate_chain.evaluate(
                candidate,
                dt=pd.Timestamp(getattr(bar, "datetime", pd.Timestamp.now())),
                state_provider=self.state_provider,
                original_lots=max(1, int(lots)),
            )
        except Exception as e:  # noqa: BLE001
            self.write_log(f"entry_gate_chain error: {e}")
            return None

    def _enrich_order_from_signal_runtime(
        self,
        order: dict[str, Any],
        bar: BarData,
        *,
        symbol: str,
        exchange: str,
    ) -> dict[str, Any]:
        ctx = self.signal_generator_context if isinstance(self.signal_generator_context, dict) else {}
        generate_fn = ctx.get("generate_fn")
        if not callable(generate_fn):
            return order
        needs = ("trade_filter_prob", "trade_filter_prob_pctl", "final_decision_score")
        if all(order.get(k) is not None for k in needs):
            return order
        interval = str(getattr(self, "interval", "") or order.get("interval", "")).strip()
        side = str(order.get("side", "")).strip().lower()
        bars = {
            (symbol, interval): {
                "datetime": getattr(bar, "datetime", ""),
                "symbol": symbol,
                "exchange": exchange,
                "interval": interval,
                "open": float(getattr(bar, "open_price", 0.0)),
                "high": float(getattr(bar, "high_price", 0.0)),
                "low": float(getattr(bar, "low_price", 0.0)),
                "close": float(getattr(bar, "close_price", 0.0)),
                "volume": float(getattr(bar, "volume", 0.0)),
                "side": side,
                "signal_type": str(order.get("signal_type", "")),
            }
        }
        kwargs: dict[str, Any] = {
            "bars_by_symbol_interval": bars,
            "cfg": ctx.get("cfg"),
            "as_of": pd.Timestamp(getattr(bar, "datetime", pd.Timestamp.now())),
            "model_registry": ctx.get("model_registry"),
            "feature_loader": ctx.get("feature_loader"),
            "score_manifest": ctx.get("score_manifest"),
        }
        try:
            out = generate_fn(**kwargs)
        except Exception as e:  # noqa: BLE001
            self.write_log(f"signal_generator error: {e}")
            return order
        if not isinstance(out, pd.DataFrame) or out.empty:
            return order
        row = out.loc[out.get("side", pd.Series([], dtype=object)).astype(str).str.lower() == side]
        picked = row.iloc[0] if not row.empty else out.iloc[0]
        enriched = dict(order)
        for k in needs:
            v = picked.get(k)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                enriched[k] = v
        st = str(picked.get("signal_type", "")).strip()
        if st:
            enriched["signal_type"] = st
        return enriched

    def _update_position_context_from_trade(self, trade: Any) -> None:
        direction = self._normalize_direction(str(getattr(getattr(trade, "direction", None), "value", "")).strip())
        offset = self._normalize_offset(str(getattr(getattr(trade, "offset", None), "value", "")).strip())
        price = float(getattr(trade, "price", 0.0) or 0.0)
        symbol = str(getattr(trade, "symbol", "")).strip().upper()
        exchange = str(getattr(getattr(trade, "exchange", None), "value", "")).strip().upper()
        if offset == "open" and direction in {"long", "short"} and price > 0:
            self._open_position_ctx = {"side": direction, "entry_price": price, "symbol": symbol, "exchange": exchange, "interval": str(getattr(self, "interval", ""))}
            return
        if offset == "close" and int(self.pos) == 0:
            self._open_position_ctx = None

    @staticmethod
    def _normalize_direction(raw: str) -> str:
        v = str(raw or "").strip().lower()
        return "long" if v in {"long", "多", "buy"} else "short" if v in {"short", "空", "sell"} else v

    @staticmethod
    def _normalize_offset(raw: str) -> str:
        v = str(raw or "").strip().lower()
        return "open" if v in {"open", "开"} else "close" if v in {"close", "平", "平仓", "closetoday", "closeyesterday"} else v

    def _maybe_apply_position_evaluator(self, bar: BarData) -> bool:
        if self.position_evaluator is None:
            return False
        pos = int(self.pos)
        if pos == 0:
            return False
        ctx = dict(self._open_position_ctx or {})
        entry_price = float(ctx.get("entry_price", 0.0) or 0.0)
        if entry_price <= 0:
            return False
        position = {"side": "long" if pos > 0 else "short", "entry_price": entry_price, "lots": abs(pos)}
        bar_row = {"datetime": getattr(bar, "datetime", ""), "open": float(getattr(bar, "open_price", 0.0)), "high": float(getattr(bar, "high_price", 0.0)), "low": float(getattr(bar, "low_price", 0.0)), "close": float(getattr(bar, "close_price", 0.0))}
        try:
            decision = self.position_evaluator.evaluate(
                position,
                bar_row,
                cluster=str(ctx.get("cluster", "")),
                interval=str(ctx.get("interval", getattr(self, "interval", ""))),
            )
        except Exception as e:  # noqa: BLE001
            self.write_log(f"position_evaluator error: {e}")
            return False
        if not bool(getattr(decision, "should_exit", False)):
            return False
        exit_px = float(getattr(decision, "exit_price", float("nan")))
        if not math.isfinite(exit_px) or exit_px <= 0:
            exit_px = float(getattr(bar, "close_price", 0.0))
        self._dispatch_order(
            {
                "side": "flat",
                "lots": abs(pos),
                "order_type": "market",
                "price": exit_px,
                "signal_type": "forced_exit",
                "bypass_filters": True,
            },
            bar,
        )
        return True


__all__ = ["LegacyCtaAdapter", "bars_to_df"]
