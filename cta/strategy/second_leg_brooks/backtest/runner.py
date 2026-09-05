"""Run the Brooks second-leg-down strategy on actual contracts.

Everything that is not specific to this setup lives in
``cta.strategy.common.short_setup_runner``; what remains here is the config,
the candidate generator and the feature frame the replay engine needs.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from cta.strategy.common import short_setup_runner as shared_setup
from cta.strategy.multi_timeframe_trend_backtest import runner as shared_runner
from cta.strategy.second_leg_brooks.config import SecondLegBrooksConfig
from cta.strategy.second_leg_brooks.rules import build_brooks_features
from cta.strategy.second_leg_brooks.strategy import generate_brooks_candidates

DEFAULT_OUTPUT_ROOT = Path("cta/strategy/report/second_leg_brooks")
MODULE_PATH = "cta.strategy.second_leg_brooks.backtest.runner"
STRATEGY_VERSION = "second-leg-brooks-v1"


def build_parser():
    return shared_setup.build_parser(
        module_path=MODULE_PATH,
        output_root=DEFAULT_OUTPUT_ROOT,
        config_name="SecondLegBrooksConfig",
        description=__doc__,
    )


def build_reproduction_command(args):
    return shared_setup.build_reproduction_command(args, module_path=MODULE_PATH)


def run_from_args(args):
    config = SecondLegBrooksConfig(
        risk_per_trade=args.risk_per_trade,
        max_risk_per_trade=args.risk_per_trade,
        max_risk_pct=args.risk_per_trade,
        max_concurrent_positions=args.max_concurrent_positions,
        intraday_margin_utilization=args.intraday_margin_utilization,
        overnight_margin_utilization=args.overnight_margin_utilization,
        max_symbol_margin_utilization=args.max_symbol_margin_utilization,
        overnight_reduction_minutes=args.overnight_reduction_minutes,
    )
    if not args.run_id:
        args.run_id = shared_setup.default_run_id(args)
    return shared_runner.run_from_args(
        args,
        config=config,
        strategy_data_builder=_prepare_strategy_data,
        reproduction_builder=build_reproduction_command,
        context_builder=_build_run_context,
        # 出场窗口以信号周期的根数写，回放前换算成 1 分钟根数
        config_finalizer=lambda item: item.for_replay(),
    )


def _prepare_strategy_data(
    loaded,
    *,
    metadata_store,
    config: SecondLegBrooksConfig,
    start: date,
    end: date,
    initial_equity: float,
    aggregation_cache=None,
    gate_diagnostics=None,
):
    del gate_diagnostics
    minute = loaded.minute_bars.sort_values("bar_end", kind="stable").reset_index(
        drop=True
    )
    signal = shared_setup.attach_execution_instruments(
        shared_setup.signal_frame_from_minutes(minute),
        loaded=loaded,
        metadata_store=metadata_store,
    )
    signal_frame = shared_setup.signal_timeframe_bars(
        signal,
        sessions=loaded.sessions,
        minutes=config.signal_timeframe_minutes,
        aggregation_cache=aggregation_cache,
    )
    candidates = generate_brooks_candidates(
        signal_frame,
        sessions=loaded.sessions,
        instrument=shared_setup.representative_instrument(
            loaded, metadata_store=metadata_store
        ),
        config=config,
        equity=initial_equity,
        # 订单永远活在 1 分钟执行时间轴上，即使形态是 5 分钟找出来的
        execution_bars=signal if signal_frame is not signal else None,
    )
    if not candidates.empty:
        candidates = candidates.loc[
            candidates["exchange_trade_date"].map(
                lambda value: start <= value <= end
            )
        ].reset_index(drop=True)

    # 回放上下文必须逐分钟对齐，所以它始终按 1 分钟算，与信号周期无关
    context = shared_setup.replay_context(
        build_brooks_features(signal, loaded.sessions, config), signal
    )
    replay_minute = minute.merge(
        context, on="bar_end", how="left", validate="one_to_one"
    )
    daily_actual, five_actual = shared_setup.aggregated_review_bars(
        minute, sessions=loaded.sessions, aggregation_cache=aggregation_cache
    )
    return (
        candidates, daily_actual, five_actual, replay_minute, context,
        pd.DataFrame(),
    )


def _build_run_context(**values):
    return shared_setup.build_run_context(
        values,
        strategy="second_leg_brooks",
        strategy_version=STRATEGY_VERSION,
        entry_tf=f"{values['config'].signal_timeframe_minutes}min",
    )


def main(argv: Sequence[str] | None = None) -> int:
    return shared_setup.cli_main(build_parser, run_from_args, argv)


if __name__ == "__main__":
    raise SystemExit(main())
