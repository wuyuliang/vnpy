"""Tests for the missing-minute-data diagnosis and graceful degradation."""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest.runner import _missing_symbol_diagnosis
from cta.strategy.multi_timeframe_trend_backtest.runner import _turnover_table_for


def _summary(**kwargs):
    base = {
        "requested_dates": {}, "downloaded": {}, "skipped": {}, "empty": {},
        "download_errors": [],
    }
    base.update(kwargs)
    return base


def test_diagnosis_reports_per_symbol_counters() -> None:
    text = _missing_symbol_diagnosis(
        ["OI.CZCE"],
        _summary(requested_dates={"OI": 0}, downloaded={"OI": 0},
                 skipped={"OI": 0}, empty={"OI": 0}),
    )
    assert "OI.CZCE" in text
    assert "requested_dates=0" in text
    # 没有下载错误时要说明是"供应商本来就没有数据"，而不是留一片空白
    assert "主力合约映射" in text


def test_diagnosis_surfaces_the_download_error_reason() -> None:
    text = _missing_symbol_diagnosis(
        ["MA.CZCE"],
        _summary(
            requested_dates={"MA": 0},
            download_errors=[
                {"root_symbol": "MA", "stage": "mapping",
                 "reason": "no main-contract mapping in requested interval"},
            ],
        ),
    )
    assert "mapping" in text
    assert "no main-contract mapping" in text


def test_diagnosis_aggregates_repeated_reasons() -> None:
    errors = [
        {"root_symbol": "OI", "stage": "minute", "reason": "HTTP 503"}
        for _ in range(7)
    ]
    text = _missing_symbol_diagnosis(["OI.CZCE"], _summary(download_errors=errors))
    assert "×7" in text
    assert "HTTP 503" in text


def test_diagnosis_ignores_other_symbols_errors() -> None:
    text = _missing_symbol_diagnosis(
        ["OI.CZCE"],
        _summary(download_errors=[
            {"root_symbol": "MA", "stage": "mapping", "reason": "unrelated"},
        ]),
    )
    assert "unrelated" not in text


def test_diagnosis_handles_several_missing_symbols() -> None:
    text = _missing_symbol_diagnosis(
        ["OI.CZCE", "MA.CZCE"],
        _summary(requested_dates={"OI": 0, "MA": 3}, downloaded={"MA": 0},
                 empty={"MA": 3}),
    )
    assert "OI.CZCE" in text and "MA.CZCE" in text
    assert "empty=3" in text


# --------------------------------------------------------------------------
# 成交额表自动重建
# --------------------------------------------------------------------------
def _args(tmp_path, table_path, data_root=None):
    # day_root 也指向临时目录，否则重建会悄悄回落到仓库里真实的日线数据
    return argparse.Namespace(
        turnover_table=str(table_path),
        data_root=str(data_root or (tmp_path / "minute")),
        day_root=str(tmp_path / "day"),
        end="2026-07-01",
    )


def test_fresh_table_is_reused_without_rebuilding(tmp_path) -> None:
    from cta.data_code.build_symbol_turnover import DATE_SEMANTICS

    target = tmp_path / "t.parquet"
    pd.DataFrame([{
        "root_symbol": "AU", "trade_date": date(2026, 7, 1), "turnover": 1.0,
        "date_semantics": DATE_SEMANTICS,
    }]).to_parquet(target, index=False)
    out = _turnover_table_for(_args(tmp_path, target), required_through=date(2026, 7, 1))
    assert len(out) == 1


def test_stale_table_triggers_a_rebuild(tmp_path, monkeypatch) -> None:
    """表里最新日期早于回测结束日 → 视为过期，按回测区间重建并落盘。

    这里只验证"过期就重建"这一层契约：表怎么算是 build_symbol_turnover
    自己的测试范围，在单测里再造一份合法的分钟分区+交易日历只会把两层
    逻辑绑死。
    """
    from cta.data_code.build_symbol_turnover import DATE_SEMANTICS
    from cta.strategy.multi_timeframe_trend_backtest import runner as runner_module

    target = tmp_path / "t.parquet"
    pd.DataFrame([{
        "root_symbol": "AU", "trade_date": date(2025, 1, 1), "turnover": 1.0,
        "date_semantics": DATE_SEMANTICS,
    }]).to_parquet(target, index=False)

    calls: list[dict] = []
    # 重建结果要足够"像样"才会被采纳：品种数太少会被当成本机元数据不全
    rebuilt = pd.DataFrame([
        {
            "root_symbol": root, "trade_date": date(2026, 6, 30),
            "turnover": 100.0, "date_semantics": DATE_SEMANTICS,
        }
        for root in ("RB", "AU", "CU", "AG", "I", "M", "P", "Y", "TA", "MA", "OI")
    ])

    def _fake_build(**kwargs):
        calls.append(kwargs)
        return rebuilt, {"rows": 1, "roots": 1}

    monkeypatch.setattr(runner_module, "build_turnover_table", _fake_build)
    out = _turnover_table_for(
        _args(tmp_path, target, tmp_path / "minute"),
        required_through=date(2026, 7, 1),
    )

    assert len(calls) == 1
    # 重建要覆盖到回测结束日，而不是只到 start；分钟/日线根都必须用本次
    # 运行的参数，否则会悄悄读到仓库里的真实数据
    assert calls[0]["end"] == date(2026, 7, 1)
    assert str(calls[0]["minute_root"]) == str(tmp_path / "minute")
    assert str(calls[0]["day_root"]) == str(tmp_path / "day")
    # 返回并落盘的是新表，而不是继续用 2025 的旧表
    assert "RB" in set(out["root_symbol"])
    assert "RB" in set(pd.read_parquet(target)["root_symbol"])


def test_rebuild_failure_falls_back_to_the_existing_table(tmp_path) -> None:
    from cta.data_code.build_symbol_turnover import DATE_SEMANTICS

    target = tmp_path / "t.parquet"
    pd.DataFrame([{
        "root_symbol": "AU", "trade_date": date(2025, 1, 1), "turnover": 1.0,
        "date_semantics": DATE_SEMANTICS,
    }]).to_parquet(target, index=False)
    # 分钟与日线目录都不存在 → 重建产出为空 → 保留旧表而不是抛错
    out = _turnover_table_for(
        _args(tmp_path, target, tmp_path / "nope"),
        required_through=date(2026, 7, 1),
    )
    assert len(out) == 1
    assert set(out["root_symbol"]) == {"AU"}


# --------------------------------------------------------------------------
# 主力映射的逐行降级
# --------------------------------------------------------------------------
def _mapping(rows):
    return pd.DataFrame(rows, columns=["trade_date", "mapping_ts_code"])


def test_select_mapping_keeps_the_unambiguous_dates() -> None:
    from cta.strategy.brooks.cycle_v1.backtest.market_data_update import _select_mapping

    frame, dropped = _select_mapping(
        _mapping([
            ("2026-06-29", "OI2609.ZCE"),
            # 换月当天供应商给了两个合约 —— 以前整个品种直接作废
            ("2026-06-30", "OI2609.ZCE"),
            ("2026-06-30", "OI2701.ZCE"),
            ("2026-07-01", "OI2701.ZCE"),
        ]),
        start=date(2026, 6, 1),
        end=date(2026, 7, 31),
    )
    assert list(frame["trade_date"]) == ["2026-06-29", "2026-07-01"]
    assert dropped == ["2026-06-30"]


def test_select_mapping_still_filters_the_window() -> None:
    from cta.strategy.brooks.cycle_v1.backtest.market_data_update import _select_mapping

    frame, dropped = _select_mapping(
        _mapping([("2026-01-05", "MA2605.ZCE"), ("2026-06-30", "MA2609.ZCE")]),
        start=date(2026, 6, 1),
        end=date(2026, 7, 31),
    )
    assert list(frame["trade_date"]) == ["2026-06-30"]
    assert dropped == []


def test_identity_drops_foreign_rows_but_keeps_the_rest() -> None:
    from cta.strategy.brooks.cycle_v1.backtest.market_data_update import (
        _validate_mapping_identity,
    )

    frame, rejected = _validate_mapping_identity(
        _mapping([
            ("2026-06-29", "OI2609.ZCE"),
            ("2026-06-30", "RM2609.ZCE"),
            ("2026-07-01", "not-a-contract"),
        ]),
        root_symbol="OI",
        exchange="CZCE",
        mapping_exchange="ZCE",
    )
    assert list(frame["mapping_ts_code"]) == ["OI2609.ZCE"]
    assert any("root RM" in item for item in rejected)
    assert any("invalid mapped contract code" in item for item in rejected)


def test_identity_still_rejects_a_wrong_response_exchange() -> None:
    """整份响应就答错了交易所时必须硬失败，否则会把别人的数据写进本地分区。"""
    from cta.strategy.brooks.cycle_v1.backtest.market_data_update import (
        _validate_mapping_identity,
    )

    with pytest.raises(ValueError, match="mapping response exchange"):
        _validate_mapping_identity(
            _mapping([("2026-06-29", "OI2609.ZCE")]),
            root_symbol="OI",
            exchange="CZCE",
            mapping_exchange="DCE",
        )


def test_degradations_are_summarised_for_the_audit() -> None:
    from cta.strategy.brooks.cycle_v1.backtest.market_data_update import (
        _mapping_degradations,
    )

    reasons = _mapping_degradations(
        [f"2026-06-{day:02d}" for day in range(1, 10)],
        ["invalid mapped contract code: X"],
    )
    assert "dropped 9 trade date(s)" in reasons[0]
    assert "(+3)" in reasons[0]
    assert "invalid mapped contract code: X" in reasons[1]


# --------------------------------------------------------------------------
# 重建结果的采纳门槛
# --------------------------------------------------------------------------
def _rebuilt(roots, day=date(2026, 6, 30)):
    from cta.data_code.build_symbol_turnover import DATE_SEMANTICS

    return pd.DataFrame([
        {"root_symbol": root, "trade_date": day, "turnover": 1.0,
         "date_semantics": DATE_SEMANTICS}
        for root in roots
    ])


def _existing(roots, day=date(2025, 1, 1)):
    return _rebuilt(roots, day)


def test_rejection_flags_a_nearly_empty_rebuild() -> None:
    """服务器上就是这一幕：重建只剩 1 个品种，却把好表覆盖掉了。"""
    from cta.strategy.multi_timeframe_trend_backtest.runner import _rebuild_rejection

    reason = _rebuild_rejection(
        _rebuilt(["UR"]),
        previous=_existing([f"S{i}" for i in range(40)]),
        required_through=date(2026, 7, 1),
    )
    assert "only 1 root" in reason


def test_rejection_flags_a_rebuild_that_misses_the_window() -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import _rebuild_rejection

    reason = _rebuild_rejection(
        _rebuilt([f"S{i}" for i in range(20)], day=date(2026, 8, 1)),
        previous=_existing(["AU"]),
        required_through=date(2026, 7, 1),
    )
    assert "no rows before 2026-07-01" in reason


def test_rejection_flags_a_rebuild_that_lost_most_roots() -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import _rebuild_rejection

    reason = _rebuild_rejection(
        _rebuilt([f"S{i}" for i in range(12)]),
        previous=_existing([f"S{i}" for i in range(60)]),
        required_through=date(2026, 7, 1),
    )
    assert "12 root(s) versus 60" in reason


def test_a_healthy_rebuild_is_accepted() -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import _rebuild_rejection

    assert _rebuild_rejection(
        _rebuilt([f"S{i}" for i in range(60)]),
        previous=_existing([f"S{i}" for i in range(58)]),
        required_through=date(2026, 7, 1),
    ) == ""


def test_a_rejected_rebuild_does_not_overwrite_the_table(tmp_path, capsys) -> None:
    from cta.data_code.build_symbol_turnover import DATE_SEMANTICS
    from cta.strategy.multi_timeframe_trend_backtest import runner as runner_module

    target = tmp_path / "t.parquet"
    good = _rebuilt([f"S{i}" for i in range(40)], day=date(2025, 1, 1))
    good.to_parquet(target, index=False)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        runner_module,
        "build_turnover_table",
        lambda **kwargs: (_rebuilt(["UR"]), {"rows": 20, "roots": 1}),
    )
    try:
        out = _turnover_table_for(
            _args(tmp_path, target, tmp_path / "minute"),
            required_through=date(2026, 7, 1),
        )
    finally:
        monkey.undo()

    # 旧表原样保留，磁盘上也没被换掉
    assert set(out["root_symbol"]) == set(good["root_symbol"])
    assert set(pd.read_parquet(target)["root_symbol"]) == set(good["root_symbol"])
    assert "turnover_table_rebuild_rejected" in capsys.readouterr().err
    assert DATE_SEMANTICS  # 表结构没被改动


# --------------------------------------------------------------------------
# 预检：不可下载的输入要在下载之前就说清楚
# --------------------------------------------------------------------------
def _preflight_args(tmp_path, meta_root, **kwargs):
    base = {
        "meta_root": str(meta_root),
        "day_root": str(tmp_path / "day"),
        "turnover_meta_cache_root": str(tmp_path / "metacache"),
        "include_top_turnover": 0.0,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


def _complete_meta_root(tmp_path):
    from cta.strategy.brooks.scalp.metadata_importer import CANONICAL_FILENAMES

    root = tmp_path / "meta"
    root.mkdir()
    for name in ("manifest.json", *CANONICAL_FILENAMES):
        (root / name).write_text("x", encoding="utf-8")
    return root


def test_preflight_names_the_missing_metadata_files(tmp_path) -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import (
        _preflight_required_data,
    )

    root = _complete_meta_root(tmp_path)
    (root / "exchange_calendar.csv").unlink()
    with pytest.raises(ValueError) as excinfo:
        _preflight_required_data(_preflight_args(tmp_path, root))
    message = str(excinfo.value)
    assert "exchange_calendar.csv" in message
    # 要给出可执行的修复，而不是只说"缺文件"
    assert "git add -f" in message
    assert "*.csv" in message


def test_preflight_passes_with_a_complete_bundle(tmp_path, capsys) -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import (
        _preflight_required_data,
    )

    root = _complete_meta_root(tmp_path)
    (tmp_path / "day").mkdir()
    (tmp_path / "day" / "AU0.csv").write_text("x", encoding="utf-8")
    _preflight_required_data(_preflight_args(tmp_path, root))
    assert "preflight_day_data_missing" not in capsys.readouterr().err


def test_preflight_warns_about_day_data_without_blocking(tmp_path, capsys) -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import (
        _preflight_required_data,
    )

    root = _complete_meta_root(tmp_path)
    _preflight_required_data(_preflight_args(tmp_path, root))
    assert "preflight_day_data_missing" in capsys.readouterr().err


def test_preflight_warns_about_the_turnover_meta_cache_only_when_used(
    tmp_path, capsys
) -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import (
        _preflight_required_data,
    )

    root = _complete_meta_root(tmp_path)
    _preflight_required_data(_preflight_args(tmp_path, root))
    assert "preflight_turnover_meta_cache_missing" not in capsys.readouterr().err

    _preflight_required_data(
        _preflight_args(tmp_path, root, include_top_turnover=0.8)
    )
    assert "preflight_turnover_meta_cache_missing" in capsys.readouterr().err


def test_preflight_accepts_a_flat_turnover_meta_cache(tmp_path, capsys) -> None:
    """服务器上直接把 --turnover-meta-cache-root 指向 scalp/meta 就该被接受。"""
    from cta.strategy.multi_timeframe_trend_backtest.runner import (
        _preflight_required_data,
    )

    root = _complete_meta_root(tmp_path)
    _preflight_required_data(
        _preflight_args(
            tmp_path,
            root,
            include_top_turnover=0.8,
            turnover_meta_cache_root=str(root),
        )
    )
    assert "preflight_turnover_meta_cache_missing" not in capsys.readouterr().err
