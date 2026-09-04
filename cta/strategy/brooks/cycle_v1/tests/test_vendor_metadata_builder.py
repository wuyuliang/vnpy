from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest.data_loader import DiscoveredSymbol
from cta.strategy.brooks.cycle_v1.backtest.vendor_metadata_builder import (
    AuditedVendorMetadataClient,
    ContractDate,
    MetadataBuildError,
    _carry_forward_trade_rule,
    _czce_official_vendor_row,
    _czce_official_trade_rule,
    _effective_contract_mechanics,
    _exact_contract_row,
    _exact_trade_rule_row,
    _find_shfe_vendor_parameter_row,
    _load_shfe_official_vendor,
    _night_session_start_for_trade_date,
    _parse_czce_settlement_parameters_text,
    _parse_gtja_trading_rules_html,
    _published_at,
    _rebase_stale_vendor_limits,
    _session_open,
    _shfe_official_vendor_row,
    _trade_rule_limit_rate,
    _validated_historical_roll_fee_reference,
    _validated_roll_fee_reference,
    _validate_shfe_published_limit_interval,
    build_extension_frames,
    collect_contract_dates,
    match_vendor_contract,
    parse_fee_expression,
    parse_price_tick,
    prepare_execution_metadata,
    reconcile_daily_mechanics,
    session_template_from_description,
)


def test_vendor_client_normalizes_audited_empty_jin10_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def empty_snapshot(*, date: str) -> pd.DataFrame:
        del date
        raise KeyError("日期")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(futures_comm_js=empty_snapshot),
    )
    client = AuditedVendorMetadataClient()

    result = client.fetch_vendor_parameters(date(2026, 3, 23))

    assert result.empty
    assert "合约代码" in result.columns
    assert client.audit_records[0]["rows"] == []


def _gtja_next_page(
    source_date: date,
    *,
    event_content: str | None,
) -> str:
    event = (
        []
        if event_content is None
        else [{"event_date": f"{source_date:%Y%m%d}", "content": event_content}]
    )
    payload = {
        "props": {
            "pageProps": {
                "MONTH_DATA": [
                    {
                        "tradingday": f"{source_date:%Y%m%d}",
                        "tradeflag": "T",
                        "events": event,
                    }
                ]
            }
        }
    }
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script>"
    )


def test_parse_gtja_trading_rules_reads_next_data_event_table() -> None:
    table = """
    <table>
      <tr><th colspan="9">期货交易规则</th></tr>
      <tr><th>交易所</th><th>品种</th><th>代码</th>
      <th>交易保证金比例</th><th>涨跌停板幅度</th><th>合约乘数</th>
      <th>最小变动价位</th><th>限价单每笔最大下单手数</th>
      <th>特殊合约参数调整</th></tr>
      <tr><td>大商所</td><td>铁矿石</td><td>I</td><td>12%</td><td>10%</td>
      <td>100</td><td>0.5</td><td>1000</td><td></td></tr>
    </table>
    """

    result = _parse_gtja_trading_rules_html(
        _gtja_next_page(date(2026, 1, 28), event_content=table),
        date(2026, 1, 28),
    )

    assert result.iloc[0]["代码"] == "I"
    assert result.iloc[0]["涨跌停板幅度"] == 10.0
    assert result.iloc[0]["最小变动价位"] == 0.5


def test_parse_czce_settlement_parameters_reads_exact_contract_fields() -> None:
    payload = """郑州商品交易所期货结算参数表(2026-07-23)
合约代码|当日结算价|是否单边市|连续单边市天数|交易保证金率(%)|涨跌停板(%)|交易手续费|手续费收取方式|交割手续费|日内平今仓交易手续费|日持仓限额|交易限额
MA609|2,732.00|N|0|10|±9|1.00|比例值|0.00|1.00|62562|25000
"""

    result = _parse_czce_settlement_parameters_text(
        payload,
        date(2026, 7, 23),
    )

    assert result.iloc[0]["symbol"] == "MA609"
    assert result.iloc[0]["settle_price"] == "2,732.00"
    assert result.iloc[0]["margin_ratio"] == "10"
    assert result.iloc[0]["limit_ratio"] == "±9"
    assert result.iloc[0]["trade_fee"] == "1.00"
    assert result.iloc[0]["fee_type"] == "比例值"


def test_czce_official_rule_uses_short_contract_and_exact_settlement() -> None:
    parameters = _parse_czce_settlement_parameters_text(
        """郑州商品交易所期货结算参数表(2026-07-23)
合约代码|当日结算价|是否单边市|连续单边市天数|交易保证金率(%)|涨跌停板(%)|交易手续费|手续费收取方式|交割手续费|日内平今仓交易手续费|日持仓限额|交易限额
MA609|2,732.00|N|0|10|±9|1.00|比例值|0.00|1.00|62562|25000
""",
        date(2026, 7, 23),
    )

    rule = _czce_official_trade_rule(
        local_contract="MA2609.ZCE",
        root_symbol="MA",
        effective_on=date(2026, 7, 24),
        parameter_date=date(2026, 7, 23),
        price_tick=1.0,
        expected_settlement=2732.0,
        parameters=parameters,
    )

    assert rule["涨跌停板幅度"] == 9.0
    assert "交易保证金比例" not in rule
    assert rule["_czce_official_margin_rate"] == pytest.approx(0.10)
    assert _trade_rule_limit_rate(
        rule,
        local_contract="MA2609.ZCE",
        root_symbol="MA",
        exchange="CZCE",
        effective_on=date(2026, 7, 24),
        price_tick=1.0,
    ) == pytest.approx(0.09)


def test_czce_official_vendor_row_converts_absolute_pf_fees() -> None:
    parameters = _parse_czce_settlement_parameters_text(
        "header\n"
        "header2\n"
        "PF609|6792.00|N|0|10|±9|2.00|绝对值|0.00|0.00|11181|\n",
        date(2026, 7, 3),
    )

    vendor = _czce_official_vendor_row(
        local_contract="PF2609.ZCE",
        root_symbol="PF",
        source_date=date(2026, 7, 3),
        effective_on=date(2026, 7, 6),
        price_tick=2.0,
        expected_settlement=6792.0,
        parameters=parameters,
    )

    assert vendor["合约代码"] == "PF609"
    assert vendor["现价"] == pytest.approx(6792.0)
    assert vendor["涨停板"] == pytest.approx(7404.0)
    assert vendor["跌停板"] == pytest.approx(6180.0)
    assert vendor["开仓"] == "2元"
    assert vendor["平昨"] == "2元"
    assert vendor["平今"] == "0元"


def test_parse_gtja_trading_rules_blocks_missing_exact_date_snapshot() -> None:
    with pytest.raises(MetadataBuildError, match="MISSING_TRADE_RULE_SNAPSHOT"):
        _parse_gtja_trading_rules_html(
            _gtja_next_page(date(2026, 1, 29), event_content=None),
            date(2026, 1, 29),
        )


def test_exact_trade_rule_row_collapses_identical_source_duplicates() -> None:
    row = {
        "交易所": "大商所",
        "品种": "黄大豆1号",
        "代码": "A",
        "交易保证金比例": 12.0,
        "涨跌停板幅度": 6.0,
        "合约乘数": 10,
        "最小变动价位": 1.0,
        "限价单每笔最大下单手数": 1000,
        "特殊合约参数调整": pd.NA,
        "调整备注": pd.NA,
        "生效日期": "2026-03-25",
    }

    result = _exact_trade_rule_row("A", "DCE", pd.DataFrame([row, row]))

    assert result["代码"] == "A"
    assert result["交易保证金比例"] == 12.0


def test_exact_trade_rule_row_blocks_conflicting_source_duplicates() -> None:
    rows = [
        {
            "交易所": "大商所",
            "代码": "A",
            "交易保证金比例": 12.0,
            "涨跌停板幅度": 6.0,
            "生效日期": "2026-03-25",
        },
        {
            "交易所": "大商所",
            "代码": "A",
            "交易保证金比例": 15.0,
            "涨跌停板幅度": 6.0,
            "生效日期": "2026-03-25",
        },
    ]

    with pytest.raises(MetadataBuildError, match="AMBIGUOUS_TRADE_RULE"):
        _exact_trade_rule_row("A", "DCE", pd.DataFrame(rows))


def test_published_at_accepts_unchanged_field_from_earlier_snapshot() -> None:
    assert _published_at(
        "2026-02-12 23:40:43",
        date(2026, 2, 13),
        "FEE_PUBLISHED_AT",
    ) == datetime.fromisoformat("2026-02-12T23:40:43+08:00")


def test_published_at_rejects_timestamp_after_snapshot_date() -> None:
    with pytest.raises(MetadataBuildError, match="PUBLISHED_AT_AFTER_SOURCE_DATE"):
        _published_at(
            "2026-02-14 00:01:00",
            date(2026, 2, 13),
            "FEE_PUBLISHED_AT",
        )


def test_validated_roll_fee_reference_requires_exact_late_contract_match() -> None:
    frame = pd.DataFrame(
        [
            {
                "日期": "20260414",
                "合约代码": "a2607",
                "手续费公布时间": "2026-04-14 23:41:17",
                "开仓": "2元",
                "平昨": "2元",
                "平今": "2元",
            },
            {
                "日期": "20260414",
                "合约代码": "a2605",
                "手续费公布时间": "2026-04-13 21:27:15",
                "开仓": "2元",
                "平昨": "2元",
                "平今": "2元",
            },
        ]
    )

    known_at, reference_contract = _validated_roll_fee_reference(
        local_contract="A2607.DCE",
        source_date=date(2026, 4, 14),
        vendor_frame=frame,
        session_open=datetime.fromisoformat("2026-04-14T21:00:00+08:00"),
    )

    assert known_at == datetime.fromisoformat("2026-04-13T21:27:15+08:00")
    assert reference_contract == "a2605"


def test_validated_roll_fee_reference_blocks_fee_change() -> None:
    frame = pd.DataFrame(
        [
            {
                "日期": "20260414",
                "合约代码": "a2607",
                "手续费公布时间": "2026-04-14 23:41:17",
                "开仓": "3元",
                "平昨": "2元",
                "平今": "2元",
            },
            {
                "日期": "20260414",
                "合约代码": "a2605",
                "手续费公布时间": "2026-04-13 21:27:15",
                "开仓": "2元",
                "平昨": "2元",
                "平今": "2元",
            },
        ]
    )

    with pytest.raises(MetadataBuildError, match="ROLL_FEE_VALIDATION_FAILED"):
        _validated_roll_fee_reference(
            local_contract="A2607.DCE",
            source_date=date(2026, 4, 14),
            vendor_frame=frame,
            session_open=datetime.fromisoformat("2026-04-14T21:00:00+08:00"),
        )


def test_historical_roll_fee_reference_validates_prior_visible_contract() -> None:
    current = pd.DataFrame(
        [
            {
                "合约代码": "bz2604",
                "手续费公布时间": "2026-02-26 23:34:51",
                "开仓": "1/万分之",
                "平昨": "1/万分之",
                "平今": "1/万分之",
            }
        ]
    )
    historical = pd.DataFrame(
        [
            {
                "合约代码": "bz2603",
                "手续费公布时间": "2026-02-24 23:34:51",
                "开仓": "1/万分之",
                "平昨": "1/万分之",
                "平今": "1/万分之",
            }
        ]
    )

    known_at, reference = _validated_historical_roll_fee_reference(
        local_contract="BZ2604.DCE",
        source_date=date(2026, 2, 26),
        current_frame=current,
        historical_source_date=date(2026, 2, 25),
        historical_frame=historical,
        session_open=datetime.fromisoformat("2026-02-26T21:00:00+08:00"),
    )

    assert known_at == datetime.fromisoformat("2026-02-24T23:34:51+08:00")
    assert reference == "bz2603@20260225"


@pytest.mark.parametrize(
    (
        "local_contract",
        "rendered_contract",
        "different_contract",
        "fees",
        "different_fees",
        "source_date",
        "historical_source_date",
        "session_open",
        "current_known_at",
        "historical_known_at",
    ),
    [
        (
            "JM2605.DCE",
            "jm2605",
            "jm2601",
            ("1/万分之", "1/万分之", "1/万分之"),
            ("1.5/万分之", "1/万分之", "1/万分之"),
            date(2025, 12, 4),
            date(2025, 12, 3),
            "2025-12-04T21:00:00+08:00",
            "2025-12-04 23:36:52",
            "2025-12-03 23:36:52",
        ),
        (
            "A2605.DCE",
            "a2605",
            "a2603",
            ("2元", "2元", "2元"),
            ("3元", "2元", "2元"),
            date(2026, 4, 14),
            date(2026, 4, 13),
            "2026-04-14T21:00:00+08:00",
            "2026-04-14 23:41:17",
            "2026-04-13 21:27:15",
        ),
    ],
)
def test_historical_roll_fee_reference_accepts_visible_exact_contract(
    local_contract: str,
    rendered_contract: str,
    different_contract: str,
    fees: tuple[str, str, str],
    different_fees: tuple[str, str, str],
    source_date: date,
    historical_source_date: date,
    session_open: str,
    current_known_at: str,
    historical_known_at: str,
) -> None:
    current = pd.DataFrame(
        [
            {
                "合约代码": rendered_contract,
                "手续费公布时间": current_known_at,
                "开仓": fees[0],
                "平昨": fees[1],
                "平今": fees[2],
            }
        ]
    )
    historical = pd.DataFrame(
        [
            {
                "合约代码": rendered_contract,
                "手续费公布时间": historical_known_at,
                "开仓": fees[0],
                "平昨": fees[1],
                "平今": fees[2],
            },
            {
                "合约代码": different_contract,
                "手续费公布时间": historical_known_at,
                "开仓": different_fees[0],
                "平昨": different_fees[1],
                "平今": different_fees[2],
            },
        ]
    )

    known_at, reference = _validated_historical_roll_fee_reference(
        local_contract=local_contract,
        source_date=source_date,
        current_frame=current,
        historical_source_date=historical_source_date,
        historical_frame=historical,
        session_open=datetime.fromisoformat(session_open),
    )

    assert known_at == datetime.fromisoformat(
        f"{historical_known_at.replace(' ', 'T')}+08:00"
    )
    assert reference == f"{rendered_contract}@{historical_source_date:%Y%m%d}"


def test_historical_roll_fee_reference_rejects_late_exact_contract() -> None:
    current = pd.DataFrame(
        [
            {
                "合约代码": "a2605",
                "手续费公布时间": "2026-04-15 23:41:17",
                "开仓": "2元",
                "平昨": "2元",
                "平今": "2元",
            }
        ]
    )
    historical = pd.DataFrame(
        [
            {
                "合约代码": "a2605",
                "手续费公布时间": "2026-04-14 23:41:17",
                "开仓": "2元",
                "平昨": "2元",
                "平今": "2元",
            }
        ]
    )

    with pytest.raises(MetadataBuildError, match="MISSING_ROLL_FEE_REFERENCE"):
        _validated_historical_roll_fee_reference(
            local_contract="A2605.DCE",
            source_date=date(2026, 4, 15),
            current_frame=current,
            historical_source_date=date(2026, 4, 14),
            historical_frame=historical,
            session_open=datetime.fromisoformat("2026-04-14T21:00:00+08:00"),
        )


def test_carry_forward_trade_rule_requires_matching_target_absolute_limits() -> None:
    rule = {
        "生效日期": "2026-01-28",
        "交易所": "大商所",
        "代码": "A",
        "涨跌停板幅度": 6.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": None,
    }
    validation = _vendor_row()
    validation.update(
        {
            "日期": date(2026, 1, 28),
            "合约代码": "a2605",
            "现价": 4375.0,
            "涨停板": 4637.0,
            "跌停板": 4113.0,
        }
    )

    result = _carry_forward_trade_rule(
        rule_row=rule,
        local_contract="A2605.DCE",
        root_symbol="A",
        exchange="DCE",
        source_date=date(2026, 1, 28),
        effective_on=date(2026, 1, 29),
        price_tick=1.0,
        prior_settlement=4375.0,
        validation_vendor_row=validation,
    )

    assert result["生效日期"] == "2026-01-28"
    assert result["_carried_forward_to"] == "2026-01-29"
    assert result["_validation_source_date"] == "2026-01-28"


def test_gfex_carry_forward_validates_late_row_own_settlement() -> None:
    result = _carry_forward_trade_rule(
        rule_row={
            "生效日期": "2026-01-28",
            "交易所": "广期所",
            "代码": "LC",
            "涨跌停板幅度": 11.0,
            "最小变动价位": 20.0,
            "特殊合约参数调整": None,
        },
        local_contract="LC2605.GFE",
        root_symbol="LC",
        exchange="GFEX",
        source_date=date(2026, 1, 28),
        effective_on=date(2026, 1, 29),
        price_tick=20.0,
        prior_settlement=173020.0,
        validation_vendor_row={
            "日期": "20260128",
            "合约代码": "lc2605",
            "现价": 173020.0,
            "涨停板": 192040.0,
            "跌停板": 154000.0,
        },
    )

    assert result["_carried_forward_to"] == "2026-01-29"
    assert result["_validation_source_date"] == "2026-01-28"


def test_carry_forward_trade_rule_rejects_mismatched_target_limits() -> None:
    validation = _vendor_row()
    validation.update(
        {
            "日期": date(2026, 1, 28),
            "合约代码": "a2605",
            "现价": 4375.0,
            "涨停板": 4638.0,
            "跌停板": 4113.0,
        }
    )

    with pytest.raises(
        MetadataBuildError,
        match="CARRY_FORWARD_LIMIT_VALIDATION_FAILED",
    ):
        _carry_forward_trade_rule(
            rule_row={
                "生效日期": "2026-01-28",
                "交易所": "大商所",
                "代码": "A",
                "涨跌停板幅度": 6.0,
                "最小变动价位": 1.0,
                "特殊合约参数调整": None,
            },
            local_contract="A2605.DCE",
            root_symbol="A",
            exchange="DCE",
            source_date=date(2026, 1, 28),
            effective_on=date(2026, 1, 29),
            price_tick=1.0,
            prior_settlement=4375.0,
            validation_vendor_row=validation,
        )


def test_carry_forward_trade_rule_validates_shfe_absolute_rounding() -> None:
    validation = {
        "日期": "20251231",
        "合约代码": "ad2603",
        "现价": 21480.0,
        "涨停板": 22550.0,
        "跌停板": 20405.0,
    }

    result = _carry_forward_trade_rule(
        rule_row={
            "生效日期": "2025-12-31",
            "交易所": "上期所",
            "代码": "AD",
            "涨跌停板幅度": 5.0,
            "最小变动价位": 5.0,
            "特殊合约参数调整": None,
        },
        local_contract="AD2603.SHF",
        root_symbol="AD",
        exchange="SHFE",
        source_date=date(2025, 12, 31),
        effective_on=date(2026, 1, 5),
        price_tick=5.0,
        prior_settlement=21480.0,
        validation_vendor_row=validation,
    )

    assert result["_carried_forward_to"] == "2026-01-05"


def test_carry_forward_trade_rule_validates_czce_outward_rounding() -> None:
    result = _carry_forward_trade_rule(
        rule_row={
            "生效日期": "2026-01-28",
            "交易所": "郑商所",
            "代码": "CF",
            "涨跌停板幅度": 6.0,
            "最小变动价位": 5.0,
            "特殊合约参数调整": None,
        },
        local_contract="CF2605.ZCE",
        root_symbol="CF",
        exchange="CZCE",
        source_date=date(2026, 1, 28),
        effective_on=date(2026, 1, 29),
        price_tick=5.0,
        prior_settlement=14755.0,
        validation_vendor_row={
            "日期": "20260128",
            "合约代码": "CF605",
            "现价": 14755.0,
            "涨停板": 15645.0,
            "跌停板": 13865.0,
        },
    )

    assert result["_carried_forward_to"] == "2026-01-29"
    assert _trade_rule_limit_rate(
        result,
        local_contract="CF2605.ZCE",
        root_symbol="CF",
        exchange="CZCE",
        effective_on=date(2026, 1, 29),
        price_tick=5.0,
    ) == pytest.approx(0.06)


def test_shfe_carry_forward_uses_officially_validated_published_limits() -> None:
    validation = {
        "日期": "20260128",
        "合约代码": "al2603",
        "现价": 24865.0,
        "涨停板": 27100.0,
        "跌停板": 22625.0,
    }
    official = {
        "_source_date": "2026-01-28",
        "INSTRUMENTID": "al2603",
        "SETTLEMENTPRICE": 24865.0,
    }

    result = _carry_forward_trade_rule(
        rule_row={
            "生效日期": "2026-01-28",
            "交易所": "上期所",
            "代码": "AL",
            "涨跌停板幅度": 7.0,
            "最小变动价位": 5.0,
            "特殊合约参数调整": (
                "AL2603合约交易保证金比例为17.0%，涨跌幅度为8.0%"
            ),
        },
        local_contract="AL2603.SHF",
        root_symbol="AL",
        exchange="SHFE",
        source_date=date(2026, 1, 28),
        effective_on=date(2026, 1, 29),
        price_tick=5.0,
        prior_settlement=24865.0,
        validation_vendor_row=validation,
        official_parameter_row=official,
    )

    rate = _trade_rule_limit_rate(
        result,
        local_contract="AL2603.SHF",
        root_symbol="AL",
        exchange="SHFE",
        effective_on=date(2026, 1, 29),
        price_tick=5.0,
    )
    assert rate == pytest.approx(0.09, abs=2e-5)
    assert result["_published_limit_validation"] == "SHFE_OFFICIAL_JS_JIN10"


def test_ine_carry_forward_uses_officially_validated_published_limits() -> None:
    validation = {
        "日期": "20260128",
        "合约代码": "bc2603",
        "现价": 90910.0,
        "涨停板": 99090.0,
        "跌停板": 82720.0,
    }
    official = {
        "_source_date": "2026-01-28",
        "INSTRUMENTID": "bc2603",
        "SETTLEMENTPRICE": 90910.0,
    }

    result = _carry_forward_trade_rule(
        rule_row={
            "生效日期": "2026-01-28",
            "交易所": "能源中心",
            "代码": "BC",
            "涨跌停板幅度": 7.0,
            "最小变动价位": 10.0,
            "特殊合约参数调整": (
                "BC2603合约交易保证金比例为17.0%，涨跌幅度为8.0%"
            ),
        },
        local_contract="BC2603.INE",
        root_symbol="BC",
        exchange="INE",
        source_date=date(2026, 1, 28),
        effective_on=date(2026, 1, 29),
        price_tick=10.0,
        prior_settlement=90910.0,
        validation_vendor_row=validation,
        official_parameter_row=official,
    )

    rate = _trade_rule_limit_rate(
        result,
        local_contract="BC2603.INE",
        root_symbol="BC",
        exchange="INE",
        effective_on=date(2026, 1, 29),
        price_tick=10.0,
    )
    assert rate == pytest.approx(0.09, abs=6e-5)
    assert result["_published_limit_validation"] == "INE_OFFICIAL_JS_JIN10"


def test_trade_rule_limit_rate_resolves_same_root_contract_range() -> None:
    row = {
        "生效日期": "2026-02-11",
        "交易所": "上期所",
        "代码": "AD",
        "涨跌停板幅度": 5.0,
        "最小变动价位": 5.0,
        "特殊合约参数调整": (
            "AD2603-AD2701合约交易保证金比例为17.0%，涨跌幅度为8.0%"
        ),
    }

    assert _trade_rule_limit_rate(
        row,
        local_contract="AD2604.SHF",
        root_symbol="AD",
        exchange="SHFE",
        effective_on=date(2026, 2, 11),
        price_tick=5.0,
    ) == pytest.approx(0.08)
    assert _trade_rule_limit_rate(
        row,
        local_contract="AD2702.SHF",
        root_symbol="AD",
        exchange="SHFE",
        effective_on=date(2026, 2, 11),
        price_tick=5.0,
    ) == pytest.approx(0.05)


def test_trade_rule_limit_rate_resolves_slash_separated_contracts() -> None:
    row = {
        "生效日期": "2026-03-09",
        "交易所": "上期所",
        "代码": "AG",
        "涨跌停板幅度": 14.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": (
            "AG2603/AG2604/AG2605/AG2606/AG2607/ AG2608/AG2609/AG2610/"
            "AG2611/AG2612/AG2701/ AG2702合约交易保证金比例为38.0%，"
            "涨跌幅度为20.0%"
        ),
    }

    assert _trade_rule_limit_rate(
        row,
        local_contract="AG2606.SHF",
        root_symbol="AG",
        exchange="SHFE",
        effective_on=date(2026, 3, 9),
        price_tick=1.0,
    ) == pytest.approx(0.20)
    assert _trade_rule_limit_rate(
        row,
        local_contract="AG2703.SHF",
        root_symbol="AG",
        exchange="SHFE",
        effective_on=date(2026, 3, 9),
        price_tick=1.0,
    ) == pytest.approx(0.14)


def test_shfe_official_parameters_override_conflicting_gtja_limit_rate() -> None:
    gtja_row = {
        "生效日期": "2026-02-26",
        "交易所": "上期所",
        "代码": "AD",
        "涨跌停板幅度": 5.0,
        "最小变动价位": 5.0,
        "特殊合约参数调整": (
            "AD2603-AD2702合约交易保证金比例为17.0%，涨跌幅度为16.0%"
        ),
    }
    official_row = {
        "INSTRUMENTID": "ad2604",
        "SETTLEMENTPRICE": 22510.0,
        "SPECLONGMARGINRATIO": 0.10,
        "SPECSHORTMARGINRATIO": 0.10,
        "_source_date": "2026-02-25",
    }

    vendor_row = {
        "日期": "2026-02-25",
        "合约代码": "ad2604",
        "现价": 22510.0,
        "涨停板": 24310.0,
        "跌停板": 20705.0,
        "保证金/买开": "10%",
        "保证金/卖开": "10%",
        "开仓": "0.5/万分之(11.3元)",
        "平昨": "0.5/万分之(11.3元)",
        "平今": "0/万分之(0元)",
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="AD2604.SHF",
        root_symbol="AD",
        exchange="SHFE",
        trade_date=date(2026, 2, 26),
        prior_open_date=date(2026, 2, 25),
        price_tick=5.0,
        current_settlement_row=_settlement_row(
            ts_code="AD2604.SHF",
            trade_date="20260226",
            settle=22725.0,
            margin=0.10,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="AD2604.SHF",
            trade_date="20260225",
            settle=22510.0,
            margin=0.10,
        ),
        vendor_row=vendor_row,
        vendor_source_date=date(2026, 2, 25),
        prior_trade_rule_row=gtja_row,
        current_trade_rule_row=gtja_row,
        prior_shfe_parameter_row=official_row,
        current_shfe_parameter_row=official_row,
        session_open=datetime.fromisoformat("2026-02-25T21:00:00+08:00"),
        roll_entry_validation=True,
        fee_reference_contract="AD2603",
    )

    assert mechanics.daily["limit_up"] == 24310.0
    assert mechanics.daily["limit_down"] == 20705.0
    assert "SHFE_OFFICIAL_JS" in mechanics.daily["limit_rounding_rule"]


def test_shfe_roll_preserves_published_limits_when_margin_gap_is_not_two_pp() -> None:
    gtja_row = {
        "生效日期": "2026-02-02",
        "交易所": "上期所",
        "代码": "AD",
        "涨跌停板幅度": 5.0,
        "最小变动价位": 5.0,
        "特殊合约参数调整": (
            "AD2603合约交易保证金比例为17.0%，涨跌幅度为7.0%"
        ),
    }
    official_row = {
        "INSTRUMENTID": "ad2603",
        "SETTLEMENTPRICE": 23485.0,
        "SPECLONGMARGINRATIO": 0.10,
        "SPECSHORTMARGINRATIO": 0.10,
        "_source_date": "2026-01-30",
    }
    vendor_row = {
        "日期": "2026-01-30",
        "合约代码": "ad2603",
        "现价": 23485.0,
        "涨停板": 25125.0,
        "跌停板": 21840.0,
        "保证金/买开": "10%",
        "保证金/卖开": "10%",
        "开仓": "0.5/万分之(11.7元)",
        "平昨": "0.5/万分之(11.7元)",
        "平今": "0/万分之(0元)",
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="AD2603.SHF",
        root_symbol="AD",
        exchange="SHFE",
        trade_date=date(2026, 2, 2),
        prior_open_date=date(2026, 1, 30),
        price_tick=5.0,
        current_settlement_row=_settlement_row(
            ts_code="AD2603.SHF",
            trade_date="20260202",
            settle=22490.0,
            margin=0.10,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="AD2603.SHF",
            trade_date="20260130",
            settle=23485.0,
            margin=0.10,
        ),
        vendor_row=vendor_row,
        vendor_source_date=date(2026, 1, 30),
        prior_trade_rule_row=gtja_row,
        current_trade_rule_row=gtja_row,
        prior_shfe_parameter_row=official_row,
        current_shfe_parameter_row=official_row,
        session_open=datetime.fromisoformat("2026-02-02T09:00:00+08:00"),
        roll_entry_validation=True,
        fee_reference_contract="AD2604",
    )

    assert mechanics.daily["limit_up"] == 25125.0
    assert mechanics.daily["limit_down"] == 21840.0
    assert mechanics.daily["limit_rounding_rule"] == (
        "PUBLISHED_ABSOLUTE_LIMITS_SHFE_OFFICIAL_JS_ROLL_VALIDATED"
    )


def test_shfe_rebase_calibrates_published_limits_not_conflicting_prior_gtja() -> None:
    prior_gtja = {
        "生效日期": "2026-02-26",
        "交易所": "上期所",
        "代码": "AD",
        "涨跌停板幅度": 5.0,
        "最小变动价位": 5.0,
        "特殊合约参数调整": (
            "AD2603-AD2702合约交易保证金比例为17.0%，涨跌幅度为16.0%"
        ),
    }
    current_gtja = dict(prior_gtja)
    current_gtja["生效日期"] = "2026-02-27"
    current_gtja["特殊合约参数调整"] = (
        "AD2604合约交易保证金比例为17.0%，涨跌幅度为8.0%"
    )

    rate, limit_up, limit_down, rounding = _rebase_stale_vendor_limits(
        local_contract="AD2604.SHF",
        root_symbol="AD",
        exchange="SHFE",
        prior_open_date=date(2026, 2, 25),
        trade_date=date(2026, 2, 27),
        price_tick=5.0,
        prior_settlement=22725.0,
        vendor_settlement=22510.0,
        vendor_row={"涨停板": 24310.0, "跌停板": 20705.0},
        prior_trade_rule_row=prior_gtja,
        current_trade_rule_row=current_gtja,
    )

    assert rate == pytest.approx(0.08)
    assert limit_up == 24540.0
    assert limit_down == 20905.0
    assert "PUBLISHED_INTERVAL" in rounding


def test_czce_rebase_uses_outward_absolute_price_rounding() -> None:
    rule = {
        "生效日期": "2026-01-05",
        "交易所": "郑商所",
        "代码": "AP",
        "涨跌停板幅度": 9.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": None,
    }

    rate, limit_up, limit_down, rounding = _rebase_stale_vendor_limits(
        local_contract="AP2605.ZCE",
        root_symbol="AP",
        exchange="CZCE",
        prior_open_date=date(2025, 12, 31),
        trade_date=date(2026, 1, 5),
        price_tick=1.0,
        prior_settlement=9161.0,
        vendor_settlement=9159.0,
        vendor_row={"涨停板": 9984.0, "跌停板": 8334.0},
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
    )

    assert rate == pytest.approx(0.09)
    assert limit_up == 9986.0
    assert limit_down == 8336.0
    assert rounding == (
        "GTJA_EFFECTIVE_RATE_JIN10_"
        "CZCE_PUBLISHED_INTERVAL_OUTWARD_ABSOLUTE_REBASED"
    )


def test_dce_rebase_calibrates_published_dynamic_amplitude() -> None:
    current_rule = {
        "生效日期": "2026-03-10",
        "交易所": "大商所",
        "代码": "BZ",
        "涨跌停板幅度": 7.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": (
            "BZ2604合约交易保证金比例为24.0%，涨跌幅度为12.0%"
        ),
    }

    rate, limit_up, limit_down, rounding = _rebase_stale_vendor_limits(
        local_contract="BZ2604.DCE",
        root_symbol="BZ",
        exchange="DCE",
        prior_open_date=date(2026, 3, 9),
        trade_date=date(2026, 3, 10),
        price_tick=1.0,
        prior_settlement=8279.0,
        vendor_settlement=7414.0,
        vendor_row={"涨停板": 8155.0, "跌停板": 6673.0},
        prior_trade_rule_row=current_rule,
        current_trade_rule_row=current_rule,
    )

    assert rate == pytest.approx(0.12)
    assert limit_up == 9272.0
    assert limit_down == 7286.0
    assert "DCE_PUBLISHED_INTERVAL_FLOOR_AMPLITUDE" in rounding


def test_gfex_rebase_uses_symmetric_floor_amplitude() -> None:
    rule = {
        "生效日期": "2026-01-05",
        "交易所": "广期所",
        "代码": "LC",
        "涨跌停板幅度": 10.0,
        "最小变动价位": 20.0,
        "特殊合约参数调整": None,
    }

    rate, limit_up, limit_down, rounding = _rebase_stale_vendor_limits(
        local_contract="LC2605.GFE",
        root_symbol="LC",
        exchange="GFEX",
        prior_open_date=date(2025, 12, 31),
        trade_date=date(2026, 1, 5),
        price_tick=20.0,
        prior_settlement=120640.0,
        vendor_settlement=120540.0,
        vendor_row={"涨停板": 132580.0, "跌停板": 108500.0},
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
    )

    assert rate == pytest.approx(0.10)
    assert limit_up == 132700.0
    assert limit_down == 108580.0
    assert rounding == (
        "GTJA_EFFECTIVE_RATE_JIN10_"
        "GFEX_PUBLISHED_INTERVAL_FLOOR_AMPLITUDE_REBASED"
    )


def test_czce_rebase_calibrates_contract_level_dynamic_expansion() -> None:
    rule = {
        "生效日期": "2026-03-23",
        "交易所": "郑商所",
        "代码": "AP",
        "涨跌停板幅度": 9.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": None,
    }

    rate, limit_up, limit_down, rounding = _rebase_stale_vendor_limits(
        local_contract="AP2605.ZCE",
        root_symbol="AP",
        exchange="CZCE",
        prior_open_date=date(2026, 3, 20),
        trade_date=date(2026, 3, 23),
        price_tick=1.0,
        prior_settlement=10670.0,
        vendor_settlement=10652.0,
        vendor_row={"涨停板": 12037.0, "跌停板": 9267.0},
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
    )

    assert rate == pytest.approx(0.09)
    assert limit_up == 11631.0
    assert limit_down == 9709.0
    assert "CZCE_PUBLISHED_INTERVAL" in rounding


def test_shfe_rebase_uses_current_official_margin_and_fee_schedule() -> None:
    vendor_row = {
        "日期": "2025-12-31",
        "合约代码": "al2602",
        "现价": 22420.0,
        "涨停板": 23985.0,
        "跌停板": 20850.0,
        "保证金/买开": "9%",
        "保证金/卖开": "9%",
        "开仓": "3元",
        "平昨": "3元",
        "平今": "3元",
    }
    rule = {
        "生效日期": "2026-01-05",
        "交易所": "上期所",
        "代码": "AL",
        "涨跌停板幅度": 7.0,
        "最小变动价位": 5.0,
        "特殊合约参数调整": None,
    }
    prior_official = {
        "INSTRUMENTID": "al2602",
        "SETTLEMENTPRICE": 22420.0,
        "SPECLONGMARGINRATIO": 0.09,
        "SPECSHORTMARGINRATIO": 0.09,
        "TRADEFEERATIO": 0.0,
        "TRADEFEEUNIT": 3.0,
        "TTRADEFEERATIO": 0.0,
        "TTRADEFEEUNIT": 1.5,
        "_source_date": "2025-12-30",
    }
    current_official = dict(prior_official)
    current_official.update(
        {
            "SETTLEMENTPRICE": 22740.0,
            "SPECLONGMARGINRATIO": 0.10,
            "SPECSHORTMARGINRATIO": 0.10,
            "_source_date": "2025-12-31",
        }
    )

    mechanics = reconcile_daily_mechanics(
        local_contract="AL2602.SHF",
        root_symbol="AL",
        exchange="SHFE",
        trade_date=date(2026, 1, 5),
        prior_open_date=date(2025, 12, 31),
        price_tick=5.0,
        current_settlement_row=_settlement_row(
            ts_code="AL2602.SHF",
            trade_date="20260105",
            settle=22900.0,
            margin=0.10,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="AL2602.SHF",
            trade_date="20251231",
            settle=22740.0,
            margin=0.10,
        ),
        vendor_row=vendor_row,
        vendor_source_date=date(2025, 12, 31),
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
        prior_shfe_parameter_row=prior_official,
        current_shfe_parameter_row=current_official,
        session_open=datetime.fromisoformat("2026-01-05T09:00:00+08:00"),
    )

    assert mechanics.fee["margin_rate_long"] == pytest.approx(0.10)
    assert mechanics.fee["fee_per_lot_open"] == 3.0
    assert mechanics.fee["fee_per_lot_close_today"] == 1.5
    assert "SHFE_OFFICIAL_JS_FEE" in mechanics.fee["source"]


def test_shfe_matching_settlement_uses_current_official_margin_and_fees() -> None:
    official = {
        "INSTRUMENTID": "br2602",
        "SETTLEMENTPRICE": 11550.0,
        "SPECLONGMARGINRATIO": 0.10,
        "SPECSHORTMARGINRATIO": 0.10,
        "TRADEFEERATIO": 0.02,
        "TRADEFEEUNIT": 0.0,
        "TTRADEFEERATIO": 0.01,
        "TTRADEFEEUNIT": 0.0,
        "_source_date": "2025-12-31",
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="BR2602.SHF",
        root_symbol="BR",
        exchange="SHFE",
        trade_date=date(2026, 1, 5),
        prior_open_date=date(2025, 12, 31),
        price_tick=5.0,
        current_settlement_row=_settlement_row(
            ts_code="BR2602.SHF",
            trade_date="20260105",
            settle=11600.0,
            margin=0.12,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="BR2602.SHF",
            trade_date="20251231",
            settle=11550.0,
            margin=0.12,
        ),
        vendor_row={
            "日期": "2025-12-31",
            "合约代码": "br2602",
            "现价": 11550.0,
            "涨停板": 12355.0,
            "跌停板": 10740.0,
            "保证金/买开": "9%",
            "保证金/卖开": "9%",
            "开仓": "0.2/万分之",
            "平昨": "0.2/万分之",
            "平今": "0.2/万分之",
        },
        vendor_source_date=date(2025, 12, 31),
        current_shfe_parameter_row=official,
        session_open=datetime.fromisoformat("2026-01-05T09:00:00+08:00"),
    )

    assert mechanics.fee["margin_rate_long"] == pytest.approx(0.10)
    assert mechanics.fee["open_fee_rate"] == pytest.approx(0.00002)
    assert mechanics.fee["close_today_fee_rate"] == pytest.approx(0.00001)
    assert "SHFE_OFFICIAL_JS_FEE" in mechanics.fee["source"]


def test_ine_rebase_uses_current_official_margin_and_fee_schedule() -> None:
    rule = {
        "生效日期": "2026-01-05",
        "交易所": "能源中心",
        "代码": "BC",
        "涨跌停板幅度": 7.0,
        "最小变动价位": 10.0,
        "特殊合约参数调整": "BC2602合约交易保证金比例为17.0%",
    }
    prior_official = {
        "INSTRUMENTID": "bc2602",
        "SETTLEMENTPRICE": 87070.0,
        "SPECLONGMARGINRATIO": 0.09,
        "SPECSHORTMARGINRATIO": 0.09,
        "TRADEFEERATIO": 0.01,
        "TRADEFEEUNIT": 0.0,
        "TTRADEFEERATIO": 0.005,
        "TTRADEFEEUNIT": 0.0,
        "_source_date": "2025-12-30",
    }
    current_official = dict(prior_official)
    current_official.update(
        {
            "SETTLEMENTPRICE": 88160.0,
            "SPECLONGMARGINRATIO": 0.10,
            "SPECSHORTMARGINRATIO": 0.10,
            "_source_date": "2025-12-31",
        }
    )
    prior_daily = _settlement_row(
        ts_code="BC2602.INE",
        trade_date="20251231",
        settle=88160.0,
        margin=0.0,
    )
    prior_daily["_source_table"] = "tushare_fut_daily"
    current_daily = _settlement_row(
        ts_code="BC2602.INE",
        trade_date="20260105",
        settle=90010.0,
        margin=0.0,
    )
    current_daily.update(
        {
            "pre_settle": 88160.0,
            "_source_table": "tushare_fut_daily",
        }
    )

    mechanics = reconcile_daily_mechanics(
        local_contract="BC2602.INE",
        root_symbol="BC",
        exchange="INE",
        trade_date=date(2026, 1, 5),
        prior_open_date=date(2025, 12, 31),
        price_tick=10.0,
        current_settlement_row=current_daily,
        prior_settlement_row=prior_daily,
        vendor_row={
            "日期": "2025-12-31",
            "合约代码": "bc2602",
            "现价": 87070.0,
            "涨停板": 93160.0,
            "跌停板": 80970.0,
            "保证金/买开": "9%",
            "保证金/卖开": "9%",
            "开仓": "0.1/万分之",
            "平昨": "0.1/万分之",
            "平今": "0/万分之",
        },
        vendor_source_date=date(2025, 12, 31),
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
        prior_shfe_parameter_row=prior_official,
        current_shfe_parameter_row=current_official,
        session_open=datetime.fromisoformat("2026-01-05T09:00:00+08:00"),
    )

    assert mechanics.daily["limit_up"] == 94330.0
    assert mechanics.daily["limit_down"] == 81980.0
    assert mechanics.fee["margin_rate_long"] == pytest.approx(0.10)
    assert mechanics.fee["open_fee_rate"] == pytest.approx(0.00001)
    assert mechanics.fee["close_today_fee_rate"] == pytest.approx(0.000005)
    assert "INE_OFFICIAL_JS_FEE" in mechanics.fee["source"]


def test_ine_roll_entry_uses_officially_validated_published_limits() -> None:
    official = {
        "INSTRUMENTID": "bc2603",
        "SETTLEMENTPRICE": 90910.0,
        "SPECLONGMARGINRATIO": 0.10,
        "SPECSHORTMARGINRATIO": 0.10,
        "TRADEFEERATIO": 0.01,
        "TRADEFEEUNIT": 0.0,
        "TTRADEFEERATIO": 0.005,
        "TTRADEFEEUNIT": 0.0,
        "_source_date": "2026-01-27",
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="BC2603.INE",
        root_symbol="BC",
        exchange="INE",
        trade_date=date(2026, 1, 28),
        prior_open_date=date(2026, 1, 27),
        price_tick=10.0,
        current_settlement_row=_settlement_row(
            ts_code="BC2603.INE",
            trade_date="20260128",
            settle=91200.0,
            margin=0.10,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="BC2603.INE",
            trade_date="20260127",
            settle=90910.0,
            margin=0.10,
        ),
        vendor_row={
            "日期": "2026-01-27",
            "合约代码": "bc2603",
            "现价": 90910.0,
            "涨停板": 98180.0,
            "跌停板": 83630.0,
            "保证金/买开": "10%",
            "保证金/卖开": "10%",
            "开仓": "0.1/万分之",
            "平昨": "0.1/万分之",
            "平今": "0.05/万分之",
        },
        vendor_source_date=date(2026, 1, 27),
        prior_shfe_parameter_row=official,
        current_shfe_parameter_row=official,
        session_open=datetime.fromisoformat("2026-01-27T21:00:00+08:00"),
        roll_entry_validation=True,
        fee_reference_contract="bc2602",
    )

    assert mechanics.daily["limit_up"] == 98180.0
    assert mechanics.daily["limit_down"] == 83630.0
    assert mechanics.daily["limit_rounding_rule"] == (
        "PUBLISHED_ABSOLUTE_LIMITS_INE_OFFICIAL_JS_ROLL_VALIDATED"
    )
    assert "INE_OFFICIAL_JS_RECONCILED" in mechanics.daily["source"]


def test_ine_official_entry_overrides_conflicting_tushare_margin() -> None:
    rule = {
        "生效日期": "2026-07-03",
        "交易所": "能源中心",
        "代码": "BC",
        "涨跌停板幅度": 7.0,
        "最小变动价位": 10.0,
        "特殊合约参数调整": (
            "BC2608合约交易保证金比例为18.0%，涨跌幅度为9.0%"
        ),
    }
    official = {
        "INSTRUMENTID": "bc2608",
        "SETTLEMENTPRICE": 90580.0,
        "SPECLONGMARGINRATIO": 0.11,
        "SPECSHORTMARGINRATIO": 0.11,
        "TRADEFEERATIO": 0.01,
        "TRADEFEEUNIT": 0.0,
        "TTRADEFEERATIO": 0.005,
        "TTRADEFEEUNIT": 0.0,
        "_source_date": "2026-07-02",
    }
    vendor = _shfe_official_vendor_row(
        official,
        local_contract="BC2608.INE",
        source_date=date(2026, 7, 2),
        price_tick=10.0,
        limit_rate=0.09,
        source_name="INE",
    )

    mechanics = reconcile_daily_mechanics(
        local_contract="BC2608.INE",
        root_symbol="BC",
        exchange="INE",
        trade_date=date(2026, 7, 3),
        prior_open_date=date(2026, 7, 2),
        price_tick=10.0,
        current_settlement_row=_settlement_row(
            ts_code="BC2608.INE",
            trade_date="20260703",
            settle=91000.0,
            margin=0.12,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="BC2608.INE",
            trade_date="20260702",
            settle=90580.0,
            margin=0.12,
        ),
        vendor_row=vendor,
        vendor_source_date=date(2026, 7, 2),
        current_trade_rule_row=rule,
        prior_shfe_parameter_row=official,
        current_shfe_parameter_row=official,
        session_open=datetime.fromisoformat("2026-07-02T21:00:00+08:00"),
        shfe_official_vendor_validation=True,
    )

    assert mechanics.fee["margin_rate_long"] == pytest.approx(0.11)
    assert mechanics.fee["margin_rate_short"] == pytest.approx(0.11)
    assert "INE_OFFICIAL_JS_TUSHARE" in mechanics.fee["source"]


def test_shfe_published_limit_interval_accepts_exact_single_rate() -> None:
    _validate_shfe_published_limit_interval(
        local_contract="AD2603.SHF",
        source_date=date(2026, 1, 20),
        settlement=100.0,
        limit_up=105.0,
        limit_down=95.0,
        price_tick=5.0,
    )


def test_shfe_official_vendor_row_preserves_fee_units_and_target_limits() -> None:
    row = _shfe_official_vendor_row(
        {
            "INSTRUMENTID": "ad2609",
            "SETTLEMENTPRICE": 22405.0,
            "SPECLONGMARGINRATIO": 0.10,
            "SPECSHORTMARGINRATIO": 0.10,
            "TRADEFEERATIO": 0.05,
            "TRADEFEEUNIT": 0.0,
            "TTRADEFEERATIO": 0.025,
            "TTRADEFEEUNIT": 0.0,
            "_source_date": "2026-07-01",
        },
        local_contract="AD2609.SHF",
        source_date=date(2026, 7, 1),
        price_tick=5.0,
        limit_rate=0.08,
    )

    assert row["涨停板"] == 24195.0
    assert row["跌停板"] == 20610.0
    assert row["保证金/买开"] == "10%"
    assert parse_fee_expression(row["开仓"]) == pytest.approx((0.00005, 0.0))
    assert parse_fee_expression(row["平今"]) == pytest.approx((0.000025, 0.0))


def test_official_vendor_loader_selects_ine_source() -> None:
    class Client:
        def __init__(self) -> None:
            self.ine_calls = 0

        def fetch_ine_settlement_parameters(self, source_date: date) -> pd.DataFrame:
            self.ine_calls += 1
            assert source_date == date(2026, 6, 29)
            return pd.DataFrame(
                [
                    {
                        "INSTRUMENTID": "bc2608",
                        "SETTLEMENTPRICE": 90970.0,
                        "SPECLONGMARGINRATIO": 0.12,
                        "SPECSHORTMARGINRATIO": 0.12,
                        "TRADEFEERATIO": 0.01,
                        "TRADEFEEUNIT": 0.0,
                        "TTRADEFEERATIO": 0.005,
                        "TTRADEFEEUNIT": 0.0,
                        "_source_date": "2026-06-29",
                    }
                ]
            )

        def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
            assert source_date == date(2026, 6, 30)
            return pd.DataFrame(
                [
                    {
                        "生效日期": "2026-06-30",
                        "交易所": "能源中心",
                        "代码": "BC",
                        "涨跌停板幅度": 7.0,
                        "最小变动价位": 10.0,
                        "特殊合约参数调整": (
                            "BC2607-BC2702合约交易保证金比例为19.0%，"
                            "涨跌幅度为10.0%"
                        ),
                    }
                ]
            )

    client = Client()
    vendor, parameter, rule, price_known_at, fee_known_at = (
        _load_shfe_official_vendor(
            source_client=client,  # type: ignore[arg-type]
            local_contract="BC2608.INE",
            root_symbol="BC",
            exchange="INE",
            source_date=date(2026, 6, 29),
            trade_date=date(2026, 6, 30),
            price_tick=10.0,
            parameter_cache={},
            rule_cache={},
        )
    )

    assert client.ine_calls == 1
    assert parameter["INSTRUMENTID"] == "bc2608"
    assert rule["代码"] == "BC"
    assert vendor["涨停板"] == 100060.0
    assert vendor["跌停板"] == 81870.0
    assert vendor["保证金/买开"] == "12%"
    assert price_known_at == datetime.fromisoformat("2026-06-29T15:30:00+08:00")
    assert fee_known_at == price_known_at


def test_shfe_vendor_parameter_search_matches_settlement_on_prior_open() -> None:
    calendar = pd.DataFrame(
        {
            "trade_date": [date(2025, 12, 30), date(2025, 12, 31)],
            "prior_open_date": [date(2025, 12, 29), date(2025, 12, 30)],
        }
    )
    calls: list[date] = []

    def fetch_parameters(source_date: date) -> pd.DataFrame:
        calls.append(source_date)
        settlement = 21625.0 if source_date == date(2025, 12, 31) else 21480.0
        return pd.DataFrame(
            {
                "INSTRUMENTID": ["ad2603"],
                "SETTLEMENTPRICE": [settlement],
                "_source_date": [source_date.isoformat()],
            }
        )

    row = _find_shfe_vendor_parameter_row(
        local_contract="AD2603.SHF",
        source_date=date(2025, 12, 31),
        expected_settlement=21480.0,
        price_tick=5.0,
        calendar=calendar,
        fetch_parameters=fetch_parameters,
        cache={},
    )

    assert row["_source_date"] == "2025-12-30"
    assert calls == [date(2025, 12, 31), date(2025, 12, 30)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1/万分之(7.4元)", (0.0001, 0.0)),
        ("0.5/万分之(3.7元)", (0.00005, 0.0)),
        ("3元", (0.0, 3.0)),
        ("0元", (0.0, 0.0)),
    ],
)
def test_parse_fee_expression_preserves_fee_basis(
    raw: str,
    expected: tuple[float, float],
) -> None:
    assert parse_fee_expression(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "--", "万分之", "3元/万分之1"])
def test_parse_fee_expression_blocks_ambiguous_values(raw: object) -> None:
    with pytest.raises(MetadataBuildError, match="FEE_EXPRESSION"):
        parse_fee_expression(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.5人民币元/吨", 0.5), (" 20 人民币元/吨", 20.0)],
)
def test_parse_price_tick_requires_leading_positive_number(
    raw: object,
    expected: float,
) -> None:
    assert parse_price_tick(raw) == expected


@pytest.mark.parametrize("raw", [None, "人民币元/吨", "0人民币元/吨", "-1元"])
def test_parse_price_tick_blocks_missing_or_nonpositive_values(raw: object) -> None:
    with pytest.raises(MetadataBuildError, match="PRICE_TICK"):
        parse_price_tick(raw)


def test_ec_official_mechanics_are_effective_dated() -> None:
    before = _effective_contract_mechanics(
        root_symbol="EC",
        exchange="INE",
        effective_on=date(2026, 5, 8),
        source_contract_size=1.0,
        source_price_tick=0.5,
    )
    after = _effective_contract_mechanics(
        root_symbol="EC",
        exchange="INE",
        effective_on=date(2026, 5, 11),
        source_contract_size=1.0,
        source_price_tick=0.5,
    )

    assert before.contract_size == pytest.approx(50.0)
    assert before.price_tick == pytest.approx(0.1)
    assert after.contract_size == pytest.approx(50.0)
    assert after.price_tick == pytest.approx(0.5)
    assert "INE_OFFICIAL_STANDARD_CONTRACT_EC" in before.source
    assert before.known_at <= datetime.fromisoformat(
        "2026-05-08T00:00:00+08:00"
    )
    assert after.known_at <= datetime.fromisoformat(
        "2026-05-11T00:00:00+08:00"
    )


def test_p_official_mechanics_ignore_current_tushare_tick_before_change() -> None:
    before = _effective_contract_mechanics(
        root_symbol="P",
        exchange="DCE",
        effective_on=date(2026, 4, 9),
        source_contract_size=10.0,
        source_price_tick=1.0,
    )
    after = _effective_contract_mechanics(
        root_symbol="P",
        exchange="DCE",
        effective_on=date(2026, 4, 10),
        source_contract_size=10.0,
        source_price_tick=2.0,
    )

    assert before.contract_size == pytest.approx(10.0)
    assert before.price_tick == pytest.approx(2.0)
    assert after.contract_size == pytest.approx(10.0)
    assert after.price_tick == pytest.approx(1.0)
    assert "DCE_OFFICIAL_STANDARD_CONTRACT_P" in before.source
    assert before.known_at <= datetime.fromisoformat(
        "2026-04-09T00:00:00+08:00"
    )
    assert after.known_at <= datetime.fromisoformat(
        "2026-04-10T00:00:00+08:00"
    )


def test_y_contract_mechanics_are_effective_dated() -> None:
    before = _effective_contract_mechanics(
        root_symbol="Y",
        exchange="DCE",
        effective_on=date(2026, 4, 9),
        source_contract_size=10.0,
        source_price_tick=1.0,
        source="DCE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC",
        source_known_at=datetime.fromisoformat("2025-05-20T00:00:00+08:00"),
    )
    after = _effective_contract_mechanics(
        root_symbol="Y",
        exchange="DCE",
        effective_on=date(2026, 4, 10),
        source_contract_size=10.0,
        source_price_tick=1.0,
        source="DCE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC",
        source_known_at=datetime.fromisoformat("2025-09-15T00:00:00+08:00"),
    )

    assert before.contract_size == pytest.approx(10.0)
    assert before.price_tick == pytest.approx(2.0)
    assert after.contract_size == pytest.approx(10.0)
    assert after.price_tick == pytest.approx(1.0)
    assert "GTJA_TRADING_RULE_20250520_Y2605" in before.source
    assert "GTJA_TRADING_RULE_20250915_Y2609" in before.source
    assert "DCE_OFFICIAL_STANDARD_CONTRACT_Y" in after.source
    assert "GTJA_TRADING_RULE_20260410_Y2609" in after.source
    assert before.known_at <= datetime.fromisoformat(
        "2026-04-09T00:00:00+08:00"
    )
    assert after.known_at <= datetime.fromisoformat(
        "2026-04-10T00:00:00+08:00"
    )


def test_match_vendor_contract_resolves_czce_short_year_code() -> None:
    assert match_vendor_contract("TA2609.ZCE", ["TA609", "MA609"]) == "TA609"


def test_match_vendor_contract_accepts_full_year_code_case_insensitively() -> None:
    assert match_vendor_contract("i2609.DCE", ["I2608", "i2609"]) == "i2609"


def test_match_vendor_contract_prefers_exact_exchange_identity() -> None:
    assert match_vendor_contract(
        "BC2604.INE",
        ["BC2604.INE", "BC2604.SHF"],
    ) == "BC2604.INE"


def test_match_vendor_contract_selects_first_case_variant() -> None:
    assert match_vendor_contract(
        "TA2609.ZCE",
        ["TA609", "ta609"],
    ) == "TA609"


def test_match_vendor_contract_selects_first_repeated_normalized_code() -> None:
    assert match_vendor_contract(
        "AG2502.SHF",
        ["ag2502", "ag2502"],
    ) == "ag2502"


def test_match_vendor_contract_blocks_distinct_fallback_contracts() -> None:
    with pytest.raises(MetadataBuildError, match="AMBIGUOUS_CONTRACT"):
        match_vendor_contract(
            "TA2609.ZCE",
            ["TA609", "TA2609.CZC"],
        )


def test_exact_contract_row_selects_first_duplicate_source_row() -> None:
    frame = pd.DataFrame(
        [
            {"合约代码": "ag2502", "现价": 7500.0},
            {"合约代码": "ag2502", "现价": 7600.0},
        ]
    )

    selected = _exact_contract_row(
        "AG2502.SHF",
        frame,
        code_column="合约代码",
    )

    assert selected["合约代码"] == "ag2502"
    assert selected["现价"] == 7500.0


@pytest.mark.parametrize(
    ("exchange", "description", "expected"),
    [
        (
            "GFEX",
            "每周一至周五，9:00-10:15、10:30-11:30，13:30-15:00",
            "CN_COMMODITY_DAY",
        ),
        (
            "DCE",
            "日盘交易：9:00～11:30，13:30～15:00；夜盘交易：21:00至23:00",
            "CN_COMMODITY_NIGHT_2300",
        ),
        (
            "DCE",
            "上午9:00-11:30,下午13:30-15:00,下午21:00-23:00(夜盘)",
            "CN_COMMODITY_NIGHT_2300",
        ),
        (
            "SHFE",
            "日盘交易：9:00－11:30，13:30－15:00；夜盘交易：21:00至次日1:00",
            "CN_COMMODITY_NIGHT_0100",
        ),
        (
            "SHFE",
            "日盘交易：9:00－11:30，13:30－15:00；夜盘交易：21:00至次日2:30",
            "CN_COMMODITY_NIGHT_0230",
        ),
    ],
)
def test_session_template_from_description_maps_declared_sessions(
    exchange: str,
    description: str,
    expected: str,
) -> None:
    assert session_template_from_description(exchange, description) == expected


@pytest.mark.parametrize("description", [None, "", "连续交易时段另行通知"])
def test_session_template_from_description_blocks_unknown_text(
    description: object,
) -> None:
    with pytest.raises(MetadataBuildError, match="SESSION_TEMPLATE"):
        session_template_from_description("DCE", description)


@pytest.mark.parametrize(
    ("root_symbol", "expected"),
    [
        ("MA", "CN_COMMODITY_NIGHT_2300"),
        ("AP", "CN_COMMODITY_DAY"),
    ],
)
def test_session_template_from_description_resolves_czce_generic_hours_by_product(
    root_symbol: str,
    expected: str,
) -> None:
    description = "上午9:00-11:30 下午1:30-3:00及交易所规定的其他交易时间"

    assert (
        session_template_from_description(
            "CZCE",
            description,
            root_symbol=root_symbol,
            effective_on=date(2026, 7, 27),
        )
        == expected
    )


def test_session_template_from_description_blocks_unknown_czce_product() -> None:
    description = "上午9:00-11:30 下午1:30-3:00及交易所规定的其他交易时间"

    with pytest.raises(MetadataBuildError, match="UNSUPPORTED_SESSION_TEMPLATE"):
        session_template_from_description(
            "CZCE",
            description,
            root_symbol="UNKNOWN",
            effective_on=date(2026, 7, 27),
        )


def test_session_template_from_description_blocks_undated_czce_mapping() -> None:
    description = "上午9:00-11:30 下午1:30-3:00及交易所规定的其他交易时间"

    with pytest.raises(MetadataBuildError, match="UNSUPPORTED_SESSION_TEMPLATE"):
        session_template_from_description(
            "CZCE",
            description,
            root_symbol="MA",
        )


def _settlement_row(
    *,
    trade_date: str,
    settle: float,
    margin: float = 0.11,
    ts_code: str = "I2609.DCE",
) -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "settle": settle,
        "long_margin_rate": margin,
        "short_margin_rate": margin,
    }


def _vendor_row() -> dict[str, object]:
    return {
        "日期": "20260724",
        "合约代码": "I2609",
        "现价": 800.0,
        "涨停板": 880.0,
        "跌停板": 720.0,
        "保证金/买开": "11%",
        "保证金/卖开": "11%",
        "开仓": "1/万分之(7.4元)",
        "平昨": "0.5/万分之(3.7元)",
        "平今": "3元",
    }


def test_reconcile_daily_mechanics_emits_canonical_rows() -> None:
    mechanics = reconcile_daily_mechanics(
        local_contract="I2609.DCE",
        root_symbol="I",
        exchange="DCE",
        trade_date=date(2026, 7, 27),
        prior_open_date=date(2026, 7, 24),
        price_tick=0.5,
        current_settlement_row=_settlement_row(
            trade_date="20260727",
            settle=805.0,
        ),
        prior_settlement_row=_settlement_row(
            trade_date="20260724",
            settle=800.0,
        ),
        vendor_row=_vendor_row(),
        session_open=datetime.fromisoformat("2026-07-24T21:00:00+08:00"),
    )

    assert mechanics.daily["contract_code"] == "I2609.DCE"
    assert mechanics.daily["pre_settlement"] == 800.0
    assert mechanics.daily["settlement"] == 805.0
    assert mechanics.daily["limit_up"] == 880.0
    assert mechanics.daily["limit_down"] == 720.0
    assert mechanics.daily["limit_rounding_rule"] == "PUBLISHED_ABSOLUTE_LIMITS"
    assert mechanics.fee["margin_rate_long"] == 0.11
    assert mechanics.fee["margin_rate_short"] == 0.11
    assert mechanics.fee["open_fee_rate"] == 0.0001
    assert mechanics.fee["close_fee_rate"] == 0.00005
    assert mechanics.fee["close_today_fee_rate"] == 0.0
    assert mechanics.fee["fee_per_lot_close_today"] == 3.0


def test_reconcile_daily_mechanics_blocks_prior_settlement_disagreement() -> None:
    prior = _settlement_row(trade_date="20260724", settle=799.0)

    with pytest.raises(MetadataBuildError, match="SETTLEMENT_MISMATCH"):
        reconcile_daily_mechanics(
            local_contract="I2609.DCE",
            root_symbol="I",
            exchange="DCE",
            trade_date=date(2026, 7, 27),
            prior_open_date=date(2026, 7, 24),
            price_tick=0.5,
            current_settlement_row=_settlement_row(
                trade_date="20260727",
                settle=805.0,
            ),
            prior_settlement_row=prior,
            vendor_row=_vendor_row(),
            session_open=datetime.fromisoformat("2026-07-24T21:00:00+08:00"),
        )


def test_reconcile_daily_mechanics_rebases_stale_vendor_limits_with_dated_rules(
) -> None:
    vendor = {
        "日期": "20251231",
        "合约代码": "A2605",
        "现价": 4204.0,
        "涨停板": 4456.0,
        "跌停板": 3952.0,
        "保证金/买开": "7%",
        "保证金/卖开": "7%",
        "开仓": "2元",
        "平昨": "2元",
        "平今": "2元",
    }
    prior_rule = {
        "生效日期": "2026-01-05",
        "交易所": "大商所",
        "代码": "A",
        "涨跌停板幅度": 6.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": "A2601合约交易保证金比例为14.0%",
    }
    current_rule = {
        "生效日期": "2026-01-05",
        "交易所": "大商所",
        "代码": "A",
        "涨跌停板幅度": 6.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": "A2601合约交易保证金比例为24.0%",
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="A2605.DCE",
        root_symbol="A",
        exchange="DCE",
        trade_date=date(2026, 1, 5),
        prior_open_date=date(2025, 12, 31),
        price_tick=1.0,
        current_settlement_row=_settlement_row(
            ts_code="A2605.DCE",
            trade_date="20260105",
            settle=4259.0,
            margin=0.07,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="A2605.DCE",
            trade_date="20251231",
            settle=4233.0,
            margin=0.07,
        ),
        vendor_row=vendor,
        prior_trade_rule_row=prior_rule,
        current_trade_rule_row=current_rule,
        session_open=datetime.fromisoformat("2026-01-05T09:00:00+08:00"),
    )

    assert mechanics.daily["limit_rate"] == pytest.approx(0.06)
    assert mechanics.daily["limit_up"] == 4486.0
    assert mechanics.daily["limit_down"] == 3980.0
    assert mechanics.daily["limit_rounding_rule"] == (
        "GTJA_EFFECTIVE_RATE_JIN10_"
        "DCE_PUBLISHED_INTERVAL_FLOOR_AMPLITUDE_REBASED"
    )


def test_dce_stale_snapshot_uses_target_prior_settlement_margin() -> None:
    rule = {
        "生效日期": "2026-06-22",
        "交易所": "大商所",
        "代码": "B",
        "涨跌停板幅度": 6.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": "B2607合约交易保证金比例为15.0%",
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="B2607.DCE",
        root_symbol="B",
        exchange="DCE",
        trade_date=date(2026, 6, 22),
        prior_open_date=date(2026, 6, 18),
        price_tick=1.0,
        current_settlement_row=_settlement_row(
            ts_code="B2607.DCE",
            trade_date="20260622",
            settle=3523.0,
            margin=0.10,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="B2607.DCE",
            trade_date="20260618",
            settle=3542.0,
            margin=0.10,
        ),
        vendor_row={
            "日期": "20260618",
            "合约代码": "b2607",
            "现价": 3537.0,
            "涨停板": 3749.0,
            "跌停板": 3325.0,
            "保证金/买开": "7%",
            "保证金/卖开": "7%",
            "开仓": "1元",
            "平昨": "1元",
            "平今": "1元",
        },
        vendor_source_date=date(2026, 6, 18),
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
        session_open=datetime.fromisoformat("2026-06-22T09:00:00+08:00"),
    )

    assert mechanics.fee["margin_rate_long"] == pytest.approx(0.10)
    assert mechanics.fee["margin_rate_short"] == pytest.approx(0.10)
    assert mechanics.daily["limit_up"] == 3754.0
    assert mechanics.daily["limit_down"] == 3330.0


def test_dce_exact_gtja_contract_margin_overrides_mirror_rates() -> None:
    rule = {
        "生效日期": "2026-06-22",
        "交易所": "大商所",
        "代码": "CS",
        "交易保证金比例": 11.0,
        "涨跌停板幅度": 5.0,
        "最小变动价位": 1.0,
        "特殊合约参数调整": "CS2607合约交易保证金比例为15.0%",
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="CS2607.DCE",
        root_symbol="CS",
        exchange="DCE",
        trade_date=date(2026, 6, 22),
        prior_open_date=date(2026, 6, 18),
        price_tick=1.0,
        current_settlement_row=_settlement_row(
            ts_code="CS2607.DCE",
            trade_date="20260622",
            settle=2712.0,
            margin=0.10,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="CS2607.DCE",
            trade_date="20260618",
            settle=2702.0,
            margin=0.10,
        ),
        vendor_row={
            "日期": "2026-06-18",
            "合约代码": "cs2607",
            "现价": 2702.0,
            "涨停板": 2837.0,
            "跌停板": 2567.0,
            "保证金/买开": "6%",
            "保证金/卖开": "6%",
            "开仓": "1.5元",
            "平昨": "1.5元",
            "平今": "1.5元",
        },
        vendor_source_date=date(2026, 6, 18),
        current_trade_rule_row=rule,
        session_open=datetime.fromisoformat("2026-06-22T09:00:00+08:00"),
    )

    assert mechanics.fee["margin_rate_long"] == pytest.approx(0.15)
    assert mechanics.fee["margin_rate_short"] == pytest.approx(0.15)
    assert "GTJA_COMPANY_MARGIN" in mechanics.fee["source"]
    assert "gtja:futures_rule:20260622" in mechanics.fee["source_url_or_file"]


def test_reconcile_daily_mechanics_marks_validated_roll_entry() -> None:
    rule = {
        "生效日期": "2026-07-27",
        "交易所": "大商所",
        "代码": "I",
        "涨跌停板幅度": 10.0,
        "最小变动价位": 0.5,
        "特殊合约参数调整": None,
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="I2609.DCE",
        root_symbol="I",
        exchange="DCE",
        trade_date=date(2026, 7, 27),
        prior_open_date=date(2026, 7, 24),
        price_tick=0.5,
        current_settlement_row=_settlement_row(
            trade_date="20260727",
            settle=805.0,
        ),
        prior_settlement_row=_settlement_row(
            trade_date="20260724",
            settle=800.0,
        ),
        vendor_row=_vendor_row(),
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
        session_open=datetime.fromisoformat("2026-07-27T09:00:00+08:00"),
        roll_entry_validation=True,
        fee_reference_contract="i2608",
    )

    assert "ROLL_ENTRY_VALIDATED" in mechanics.daily["source"]
    assert "root_fee_reference:i2608" in mechanics.daily["source_url_or_file"]


def test_reconcile_daily_mechanics_rebases_shfe_absolute_price_rounding() -> None:
    vendor = {
        "日期": "20251231",
        "合约代码": "ad2603",
        "现价": 21480.0,
        "涨停板": 22550.0,
        "跌停板": 20405.0,
        "保证金/买开": "7%",
        "保证金/卖开": "7%",
        "开仓": "0.5/万分之(10.7元)",
        "平昨": "0.5/万分之(10.7元)",
        "平今": "0/万分之(0元)",
    }
    rule = {
        "生效日期": "2026-01-05",
        "交易所": "上期所",
        "代码": "AD",
        "涨跌停板幅度": 5.0,
        "最小变动价位": 5.0,
        "特殊合约参数调整": None,
    }

    mechanics = reconcile_daily_mechanics(
        local_contract="AD2603.SHF",
        root_symbol="AD",
        exchange="SHFE",
        trade_date=date(2026, 1, 5),
        prior_open_date=date(2025, 12, 31),
        price_tick=5.0,
        current_settlement_row=_settlement_row(
            ts_code="AD2603.SHF",
            trade_date="20260105",
            settle=22480.0,
            margin=0.07,
        ),
        prior_settlement_row=_settlement_row(
            ts_code="AD2603.SHF",
            trade_date="20251231",
            settle=21625.0,
            margin=0.07,
        ),
        vendor_row=vendor,
        prior_trade_rule_row=rule,
        current_trade_rule_row=rule,
        session_open=datetime.fromisoformat("2026-01-05T09:00:00+08:00"),
    )

    assert mechanics.daily["limit_up"] == 22705.0
    assert mechanics.daily["limit_down"] == 20540.0
    assert mechanics.daily["limit_rounding_rule"] == (
        "GTJA_EFFECTIVE_RATE_JIN10_"
        "SHFE_PUBLISHED_INTERVAL_FLOOR_ABSOLUTE_REBASED"
    )


def test_session_open_skips_night_after_nontrading_calendar_gap() -> None:
    assert _session_open(
        date(2026, 1, 5),
        date(2025, 12, 31),
        "CN_COMMODITY_NIGHT_2300",
    ) == datetime.fromisoformat("2026-01-05T09:00:00+08:00")
    assert _session_open(
        date(2026, 1, 6),
        date(2026, 1, 5),
        "CN_COMMODITY_NIGHT_2300",
    ) == datetime.fromisoformat("2026-01-05T21:00:00+08:00")
    assert _session_open(
        date(2026, 1, 12),
        date(2026, 1, 9),
        "CN_COMMODITY_NIGHT_2300",
    ) == datetime.fromisoformat("2026-01-09T21:00:00+08:00")


def test_reconcile_daily_mechanics_blocks_margin_disagreement() -> None:
    with pytest.raises(MetadataBuildError, match="MARGIN_MISMATCH"):
        reconcile_daily_mechanics(
            local_contract="I2609.DCE",
            root_symbol="I",
            exchange="DCE",
            trade_date=date(2026, 7, 27),
            prior_open_date=date(2026, 7, 24),
            price_tick=0.5,
            current_settlement_row=_settlement_row(
                trade_date="20260727",
                settle=805.0,
            ),
            prior_settlement_row=_settlement_row(
                trade_date="20260724",
                settle=800.0,
                margin=0.12,
            ),
            vendor_row=_vendor_row(),
            session_open=datetime.fromisoformat("2026-07-24T21:00:00+08:00"),
        )


def test_reconcile_daily_mechanics_does_not_apply_close_margin_to_same_session(
) -> None:
    result = reconcile_daily_mechanics(
        local_contract="I2609.DCE",
        root_symbol="I",
        exchange="DCE",
        trade_date=date(2026, 7, 27),
        prior_open_date=date(2026, 7, 24),
        price_tick=0.5,
        current_settlement_row=_settlement_row(
            trade_date="20260727",
            settle=805.0,
            margin=0.12,
        ),
        prior_settlement_row=_settlement_row(
            trade_date="20260724",
            settle=800.0,
            margin=0.11,
        ),
        vendor_row=_vendor_row(),
        session_open=datetime.fromisoformat("2026-07-24T21:00:00+08:00"),
    )

    assert result.fee["margin_rate_long"] == 0.11
    assert result.fee["margin_rate_short"] == 0.11


def test_reconcile_daily_mechanics_prefers_newer_prior_settlement_margin() -> None:
    result = reconcile_daily_mechanics(
        local_contract="I2609.DCE",
        root_symbol="I",
        exchange="DCE",
        trade_date=date(2026, 7, 28),
        prior_open_date=date(2026, 7, 27),
        price_tick=0.5,
        current_settlement_row=_settlement_row(
            trade_date="20260728",
            settle=805.0,
        ),
        prior_settlement_row=_settlement_row(
            trade_date="20260727",
            settle=800.0,
            margin=0.12,
        ),
        vendor_row=_vendor_row(),
        vendor_source_date=date(2026, 7, 24),
        session_open=datetime.fromisoformat("2026-07-27T21:00:00+08:00"),
        price_known_at=datetime.fromisoformat("2026-07-24T16:00:00+08:00"),
        fee_known_at=datetime.fromisoformat("2026-07-24T16:00:00+08:00"),
    )

    assert result.fee["margin_rate_long"] == 0.12
    assert result.fee["known_at"] == "2026-07-27T15:30:00+08:00"


def test_reconcile_daily_mechanics_does_not_infer_missing_close_today_fee() -> None:
    vendor = _vendor_row()
    vendor["平今"] = None

    with pytest.raises(MetadataBuildError, match="FEE_EXPRESSION"):
        reconcile_daily_mechanics(
            local_contract="I2609.DCE",
            root_symbol="I",
            exchange="DCE",
            trade_date=date(2026, 7, 27),
            prior_open_date=date(2026, 7, 24),
            price_tick=0.5,
            current_settlement_row=_settlement_row(
                trade_date="20260727",
                settle=805.0,
            ),
            prior_settlement_row=_settlement_row(
                trade_date="20260724",
                settle=800.0,
            ),
            vendor_row=vendor,
            session_open=datetime.fromisoformat("2026-07-24T21:00:00+08:00"),
        )


class _FakeMetadataClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame:
        self.calls += 1
        del start, end
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "exchange": ["DCE", "DCE", "DCE", "DCE"],
                "cal_date": ["20260724", "20260727", "20260728", "20260729"],
                "is_open": [1, 1, 1, 1],
                "pretrade_date": [
                    "20260723",
                    "20260724",
                    "20260727",
                    "20260728",
                ],
            }
        )

    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        self.calls += 1
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "ts_code": ["I2609.DCE"],
                "exchange": ["DCE"],
                "fut_code": ["I"],
                "per_unit": [100.0],
                "quote_unit_desc": ["0.5人民币元/吨"],
                "list_date": ["20250915"],
                "delist_date": ["20260914"],
                "trade_time_desc": [
                    "日盘交易：9:00-11:30，13:30-15:00；夜盘交易：21:00至23:00"
                ],
            }
        )

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        self.calls += 1
        settle = 800.0 if trade_date == date(2026, 7, 24) else 805.0
        return pd.DataFrame(
            [_settlement_row(trade_date=f"{trade_date:%Y%m%d}", settle=settle)]
        )

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        assert source_date == date(2026, 7, 24)
        row = _vendor_row()
        row["手续费公布时间"] = "2026-07-24 16:00:00"
        row["价格公布时间"] = "2026-07-24 16:00:00"
        return pd.DataFrame([row])

    @property
    def audit_records(self) -> tuple[dict[str, object], ...]:
        return ()


class _MismatchedSettlementClient(_FakeMetadataClient):
    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        frame = super().fetch_settlements(trade_date)
        if trade_date == date(2026, 7, 24):
            frame.loc[:, "settle"] = 799.0
        return frame


class _RebasedSettlementClient(_MismatchedSettlementClient):
    def __init__(self) -> None:
        super().__init__()
        self.rule_dates: list[date] = []

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        self.rule_dates.append(source_date)
        return pd.DataFrame(
            {
                "生效日期": [source_date.isoformat()],
                "交易所": ["大商所"],
                "代码": ["I"],
                "涨跌停板幅度": [10.0],
                "最小变动价位": [0.5],
                "特殊合约参数调整": [None],
            }
        )


class _ExactMarginRuleClient(_FakeMetadataClient):
    def __init__(self) -> None:
        super().__init__()
        self.rule_dates: list[date] = []

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        self.rule_dates.append(source_date)
        return pd.DataFrame(
            {
                "生效日期": [source_date.isoformat()],
                "交易所": ["大商所"],
                "代码": ["I"],
                "交易保证金比例": [13.0],
                "涨跌停板幅度": [10.0],
                "最小变动价位": [0.5],
                "特殊合约参数调整": [
                    "I2609合约交易保证金比例为15.0%"
                ],
            }
        )


class _RollDailyMarginClient(_ExactMarginRuleClient):
    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        self.calls += 1
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "ts_code": ["I2608.DCE", "I2609.DCE"],
                "exchange": ["DCE", "DCE"],
                "fut_code": ["I", "I"],
                "per_unit": [100.0, 100.0],
                "quote_unit_desc": ["0.5人民币元/吨", "0.5人民币元/吨"],
                "list_date": ["20250915", "20250915"],
                "delist_date": ["20260814", "20260914"],
                "trade_time_desc": [
                    "日盘交易：9:00-11:30，13:30-15:00；夜盘交易：21:00至23:00",
                    "日盘交易：9:00-11:30，13:30-15:00；夜盘交易：21:00至23:00",
                ],
            }
        )

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        self.calls += 1
        rows = {
            date(2026, 7, 24): [
                _settlement_row(
                    ts_code="I2608.DCE",
                    trade_date="20260724",
                    settle=790.0,
                )
            ],
            date(2026, 7, 27): [
                _settlement_row(
                    ts_code="I2608.DCE",
                    trade_date="20260727",
                    settle=800.0,
                )
            ],
            date(2026, 7, 28): [
                _settlement_row(
                    ts_code="I2609.DCE",
                    trade_date="20260728",
                    settle=810.0,
                )
            ],
        }
        return pd.DataFrame(rows[trade_date])

    def fetch_daily_quote(
        self,
        contract_code: str,
        trade_date: date,
    ) -> pd.DataFrame:
        self.calls += 1
        assert contract_code == "I2609.DCE"
        assert trade_date == date(2026, 7, 27)
        return pd.DataFrame(
            {
                "ts_code": [contract_code],
                "trade_date": ["20260727"],
                "pre_settle": [790.0],
                "settle": [805.0],
                "_source_table": ["tushare_fut_daily"],
            }
        )

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        if source_date == date(2026, 7, 24):
            rows = [
                {
                    **_vendor_row(),
                    "日期": "20260724",
                    "合约代码": "I2608",
                    "现价": 790.0,
                    "涨停板": 869.0,
                    "跌停板": 711.0,
                    "手续费公布时间": "2026-07-24 16:00:00",
                    "价格公布时间": "2026-07-24 16:00:00",
                }
            ]
        elif source_date == date(2026, 7, 27):
            rows = [
                {
                    **_vendor_row(),
                    "日期": "20260727",
                    "合约代码": "I2608",
                    "现价": 800.0,
                    "涨停板": 880.0,
                    "跌停板": 720.0,
                    "手续费公布时间": "2026-07-27 16:00:00",
                    "价格公布时间": "2026-07-27 16:00:00",
                },
                {
                    **_vendor_row(),
                    "日期": "20260727",
                    "合约代码": "I2609",
                    "现价": 805.0,
                    "涨停板": 885.5,
                    "跌停板": 724.5,
                    "手续费公布时间": "2026-07-27 23:30:00",
                    "价格公布时间": "2026-07-27 23:45:00",
                },
            ]
        else:
            return pd.DataFrame(columns=pd.DataFrame([_vendor_row()]).columns)
        return pd.DataFrame(rows)


class _DailySettlementFallbackClient(_FakeMetadataClient):
    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        if trade_date == date(2026, 7, 27):
            self.calls += 1
            return pd.DataFrame(
                [
                    _settlement_row(
                        ts_code="J2609.DCE",
                        trade_date="20260727",
                        settle=1800.0,
                    )
                ]
            )
        return super().fetch_settlements(trade_date)

    def fetch_daily_quote(
        self,
        contract_code: str,
        trade_date: date,
    ) -> pd.DataFrame:
        self.calls += 1
        assert contract_code == "I2609.DCE"
        assert trade_date == date(2026, 7, 27)
        return pd.DataFrame(
            {
                "ts_code": [contract_code],
                "trade_date": ["20260727"],
                "pre_settle": [800.0],
                "settle": [805.0],
                "_source_table": ["tushare_fut_daily"],
            }
        )


class _FbMissingVendorClient(_FakeMetadataClient):
    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame:
        self.calls += 1
        del start, end
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "exchange": ["DCE", "DCE", "DCE"],
                "cal_date": ["20260701", "20260702", "20260703"],
                "is_open": [1, 1, 1],
                "pretrade_date": ["20260630", "20260701", "20260702"],
            }
        )

    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        self.calls += 1
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "ts_code": ["FB2609.DCE"],
                "exchange": ["DCE"],
                "fut_code": ["FB"],
                "per_unit": [10.0],
                "quote_unit_desc": ["0.5人民币元/立方米"],
                "list_date": ["20250915"],
                "delist_date": ["20260914"],
                "trade_time_desc": ["日盘交易：9:00-11:30，13:30-15:00"],
            }
        )

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        self.calls += 1
        settlements = {
            date(2026, 7, 1): 1329.0,
            date(2026, 7, 2): 1335.0,
        }
        return pd.DataFrame(
            [
                _settlement_row(
                    ts_code="FB2609.DCE",
                    trade_date=f"{trade_date:%Y%m%d}",
                    settle=settlements[trade_date],
                    margin=0.10,
                )
            ]
        )

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        assert source_date == date(2026, 7, 1)
        return pd.DataFrame(columns=pd.DataFrame([_vendor_row()]).columns)

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        assert source_date == date(2026, 7, 2)
        return pd.DataFrame(
            {
                "生效日期": [source_date.isoformat()],
                "交易所": ["大商所"],
                "代码": ["FB"],
                "交易保证金比例": [50.0],
                "涨跌停板幅度": [5.0],
                "合约乘数": [10.0],
                "最小变动价位": [0.5],
                "特殊合约参数调整": [None],
            }
        )


class _PgMissingVendorClient(_FakeMetadataClient):
    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame:
        self.calls += 1
        del start, end
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "exchange": ["DCE"] * 6,
                "cal_date": [
                    "20260320",
                    "20260323",
                    "20260324",
                    "20260325",
                    "20260326",
                    "20260327",
                ],
                "is_open": [1, 1, 1, 1, 1, 1],
                "pretrade_date": [
                    "20260319",
                    "20260320",
                    "20260323",
                    "20260324",
                    "20260325",
                    "20260326",
                ],
            }
        )

    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        self.calls += 1
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "ts_code": ["PG2605.DCE"],
                "exchange": ["DCE"],
                "fut_code": ["PG"],
                "per_unit": [20.0],
                "quote_unit_desc": ["1人民币元/吨"],
                "list_date": ["20250519"],
                "delist_date": ["20260429"],
                "trade_time_desc": [
                    "日盘交易：9:00-11:30，13:30-15:00；夜盘交易：21:00至23:00"
                ],
            }
        )

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        self.calls += 1
        settlements = {
            date(2026, 3, 23): 4000.0,
            date(2026, 3, 24): 4010.0,
            date(2026, 3, 25): 4020.0,
            date(2026, 3, 26): 4030.0,
        }
        return pd.DataFrame(
            [
                _settlement_row(
                    ts_code="PG2605.DCE",
                    trade_date=f"{trade_date:%Y%m%d}",
                    settle=settlements[trade_date],
                    margin=0.25,
                )
            ]
        )

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        if source_date == date(2026, 3, 25):
            return pd.DataFrame(
                [
                    {
                        **_vendor_row(),
                        "日期": "20260325",
                        "合约品种": "液化石油气",
                        "合约代码": "PG2605",
                        "手续费公布时间": "2026-03-25 23:39:00",
                        "价格公布时间": "2026-03-25 23:55:00",
                        "现价": 4020.0,
                        "涨停板": 4582.0,
                        "跌停板": 3458.0,
                        "保证金/买开": "25%",
                        "保证金/卖开": "25%",
                        "开仓": "12元",
                        "平昨": "6元",
                        "平今": "12元",
                    }
                ]
            )
        return pd.DataFrame(columns=pd.DataFrame([_vendor_row()]).columns)

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        assert source_date in {
            date(2026, 3, 24),
            date(2026, 3, 25),
            date(2026, 3, 26),
        }
        return pd.DataFrame(
            {
                "生效日期": [source_date.isoformat()],
                "交易所": ["大商所"],
                "代码": ["PG"],
                "交易保证金比例": [25.0],
                "涨跌停板幅度": [14.0],
                "合约乘数": [20.0],
                "最小变动价位": [1.0],
                "特殊合约参数调整": [None],
            }
        )


class _PgScopedFeeRollClient(_FakeMetadataClient):
    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame:
        self.calls += 1
        del start, end
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "exchange": ["DCE"] * 4,
                "cal_date": [
                    "20260413",
                    "20260414",
                    "20260415",
                    "20260416",
                ],
                "is_open": [1, 1, 1, 1],
                "pretrade_date": [
                    "20260410",
                    "20260413",
                    "20260414",
                    "20260415",
                ],
            }
        )

    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        self.calls += 1
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "ts_code": ["PG2605.DCE", "PG2607.DCE"],
                "exchange": ["DCE", "DCE"],
                "fut_code": ["PG", "PG"],
                "per_unit": [20.0, 20.0],
                "quote_unit_desc": ["1人民币元/吨", "1人民币元/吨"],
                "list_date": ["20250519", "20250715"],
                "delist_date": ["20260429", "20260626"],
                "trade_time_desc": [
                    "日盘交易：9:00-11:30，13:30-15:00；夜盘交易：21:00至23:00",
                    "日盘交易：9:00-11:30，13:30-15:00；夜盘交易：21:00至23:00",
                ],
            }
        )

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        self.calls += 1
        rows = {
            date(2026, 4, 13): [
                _settlement_row(
                    ts_code="PG2605.DCE",
                    trade_date="20260413",
                    settle=5000.0,
                    margin=0.16,
                )
            ],
            date(2026, 4, 14): [
                _settlement_row(
                    ts_code="PG2605.DCE",
                    trade_date="20260414",
                    settle=5010.0,
                    margin=0.16,
                ),
                _settlement_row(
                    ts_code="PG2607.DCE",
                    trade_date="20260414",
                    settle=5400.0,
                    margin=0.16,
                ),
            ],
            date(2026, 4, 15): [
                _settlement_row(
                    ts_code="PG2607.DCE",
                    trade_date="20260415",
                    settle=5450.0,
                    margin=0.16,
                )
            ],
        }
        return pd.DataFrame(rows[trade_date])

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        rows = {
            date(2026, 4, 13): [
                self._vendor_row(
                    source_date=source_date,
                    contract_code="pg2605",
                    settlement=5000.0,
                    fee_known_at="2026-04-13 20:00:00",
                    price_known_at="2026-04-13 20:00:00",
                    fees=("12元", "6元", "12元"),
                )
            ],
            date(2026, 4, 14): [
                self._vendor_row(
                    source_date=source_date,
                    contract_code="pg2605",
                    settlement=5010.0,
                    fee_known_at="2026-04-14 23:41:17",
                    price_known_at="2026-04-14 23:57:00",
                    fees=("12元", "6元", "12元"),
                )
            ],
            date(2026, 4, 15): [
                self._vendor_row(
                    source_date=source_date,
                    contract_code="pg2607",
                    settlement=5450.0,
                    fee_known_at="2026-04-15 21:31:17",
                    price_known_at="2026-04-15 23:55:00",
                    fees=("6元", "6元", "6元"),
                ),
                self._vendor_row(
                    source_date=source_date,
                    contract_code="pg2605",
                    settlement=5010.0,
                    fee_known_at="2026-04-14 23:41:17",
                    price_known_at="2026-04-15 15:00:00",
                    fees=("12元", "6元", "12元"),
                ),
            ],
        }
        return pd.DataFrame(rows[source_date])

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        assert source_date in {date(2026, 4, 14), date(2026, 4, 15)}
        return pd.DataFrame(
            {
                "生效日期": [source_date.isoformat()],
                "交易所": ["大商所"],
                "代码": ["PG"],
                "交易保证金比例": [16.0],
                "涨跌停板幅度": [14.0],
                "合约乘数": [20.0],
                "最小变动价位": [1.0],
                "特殊合约参数调整": [None],
            }
        )

    @staticmethod
    def _vendor_row(
        *,
        source_date: date,
        contract_code: str,
        settlement: float,
        fee_known_at: str,
        price_known_at: str,
        fees: tuple[str, str, str],
    ) -> dict[str, object]:
        amplitude = int(settlement * 0.14)
        return {
            "日期": f"{source_date:%Y%m%d}",
            "合约代码": contract_code,
            "手续费公布时间": fee_known_at,
            "价格公布时间": price_known_at,
            "现价": settlement,
            "涨停板": settlement + amplitude,
            "跌停板": settlement - amplitude,
            "保证金/买开": "16%",
            "保证金/卖开": "16%",
            "开仓": fees[0],
            "平昨": fees[1],
            "平今": fees[2],
        }


class _DceTargetCloseRollClient(_FakeMetadataClient):
    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame:
        self.calls += 1
        del start, end
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "exchange": ["DCE"] * 5,
                "cal_date": [
                    "20260303",
                    "20260304",
                    "20260305",
                    "20260306",
                    "20260309",
                ],
                "is_open": [1, 1, 1, 1, 1],
                "pretrade_date": [
                    "20260302",
                    "20260303",
                    "20260304",
                    "20260305",
                    "20260306",
                ],
            }
        )

    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        self.calls += 1
        assert exchange == "DCE"
        return pd.DataFrame(
            {
                "ts_code": ["JD2604.DCE", "JD2605.DCE"],
                "exchange": ["DCE", "DCE"],
                "fut_code": ["JD", "JD"],
                "per_unit": [10.0, 10.0],
                "quote_unit_desc": ["1人民币元/500千克", "1人民币元/500千克"],
                "list_date": ["20250416", "20250520"],
                "delist_date": ["20260415", "20260519"],
                "trade_time_desc": [
                    "日盘交易：9:00-11:30，13:30-15:00",
                    "日盘交易：9:00-11:30，13:30-15:00",
                ],
            }
        )

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        self.calls += 1
        rows = {
            date(2026, 3, 3): [
                _settlement_row(
                    ts_code="JD2604.DCE",
                    trade_date="20260303",
                    settle=3206.0,
                    margin=0.07,
                )
            ],
            date(2026, 3, 4): [
                _settlement_row(
                    ts_code="JD2604.DCE",
                    trade_date="20260304",
                    settle=3199.0,
                    margin=0.07,
                ),
                _settlement_row(
                    ts_code="JD2605.DCE",
                    trade_date="20260304",
                    settle=3353.0,
                    margin=0.07,
                ),
            ],
            date(2026, 3, 5): [
                _settlement_row(
                    ts_code="JD2605.DCE",
                    trade_date="20260305",
                    settle=3388.0,
                    margin=0.07,
                )
            ],
            date(2026, 3, 6): [
                _settlement_row(
                    ts_code="JD2605.DCE",
                    trade_date="20260306",
                    settle=3400.0,
                    margin=0.07,
                )
            ],
        }
        return pd.DataFrame(rows[trade_date])

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        rows = {
            date(2026, 3, 3): [
                self._vendor_row(
                    source_date=source_date,
                    contract_code="jd2604",
                    settlement=3206.0,
                    fee_known_at="2026-03-03 16:00:00",
                    price_known_at="2026-03-03 16:00:00",
                )
            ],
            date(2026, 3, 4): [
                self._vendor_row(
                    source_date=source_date,
                    contract_code="jd2604",
                    settlement=3199.0,
                    fee_known_at="2026-03-04 23:40:21",
                    price_known_at="2026-03-04 23:57:06",
                )
            ],
            date(2026, 3, 5): [
                self._vendor_row(
                    source_date=source_date,
                    contract_code="jd2605",
                    settlement=3388.0,
                    fee_known_at="2026-03-05 23:40:21",
                    price_known_at="2026-03-05 23:58:55",
                ),
                self._vendor_row(
                    source_date=source_date,
                    contract_code="jd2604",
                    settlement=3199.0,
                    fee_known_at="2026-03-04 23:40:21",
                    price_known_at="2026-03-05 15:03:12",
                ),
            ],
            date(2026, 3, 6): [
                self._vendor_row(
                    source_date=source_date,
                    contract_code="jd2605",
                    settlement=3400.0,
                    fee_known_at="2026-03-06 23:40:21",
                    price_known_at="2026-03-06 23:58:55",
                )
            ],
        }
        return pd.DataFrame(rows[source_date])

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        self.calls += 1
        assert source_date in {
            date(2026, 3, 4),
            date(2026, 3, 5),
            date(2026, 3, 6),
        }
        return pd.DataFrame(
            {
                "生效日期": [source_date.isoformat()],
                "交易所": ["大商所"],
                "代码": ["JD"],
                "交易保证金比例": [13.0],
                "涨跌停板幅度": [6.0],
                "合约乘数": [10.0],
                "最小变动价位": [1.0],
                "特殊合约参数调整": [None],
            }
        )

    @staticmethod
    def _vendor_row(
        *,
        source_date: date,
        contract_code: str,
        settlement: float,
        fee_known_at: str,
        price_known_at: str,
    ) -> dict[str, object]:
        amplitude = int(settlement * 0.06)
        return {
            "日期": f"{source_date:%Y%m%d}",
            "合约代码": contract_code,
            "手续费公布时间": fee_known_at,
            "价格公布时间": price_known_at,
            "现价": settlement,
            "涨停板": settlement + amplitude,
            "跌停板": settlement - amplitude,
            "保证金/买开": "7%",
            "保证金/卖开": "7%",
            "开仓": "1.5/万分之",
            "平昨": "1.5/万分之",
            "平今": "1.5/万分之",
        }


class _MismatchedDailyPreSettlementClient(_DailySettlementFallbackClient):
    def fetch_daily_quote(
        self,
        contract_code: str,
        trade_date: date,
    ) -> pd.DataFrame:
        frame = super().fetch_daily_quote(contract_code, trade_date)
        frame.loc[:, "pre_settle"] = 799.0
        return frame


def test_collect_contract_dates_reads_exact_local_partition_identity(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "I"
    directory.mkdir()
    pd.DataFrame(
        {
            "datetime": ["2026-07-27 09:01:00"],
            "contract_code": ["I2609.DCE"],
        }
    ).to_parquet(directory / "2026-07-27.parquet", index=False)
    symbol = DiscoveredSymbol(
        root_symbol="I",
        exchange="DCE",
        vt_symbol="I0.DCE",
        source_directory=directory,
    )

    pairs = collect_contract_dates(
        (symbol,),
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
    )

    assert len(pairs) == 1
    assert pairs[0].contract_code == "I2609.DCE"
    assert pairs[0].trade_date == date(2026, 7, 27)


def test_collect_contract_dates_keeps_only_actual_partition_contracts(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "HC"
    directory.mkdir()
    for partition_date, contract_code in (
        ("2026-04-02", "HC2605.SHF"),
        ("2026-04-03", "HC2610.SHF"),
    ):
        pd.DataFrame(
            {
                "datetime": [f"{partition_date} 09:01:00"],
                "contract_code": [contract_code],
            }
        ).to_parquet(directory / f"{partition_date}.parquet", index=False)
    symbol = DiscoveredSymbol(
        root_symbol="HC",
        exchange="SHFE",
        vt_symbol="HC0.SHFE",
        source_directory=directory,
    )

    pairs = collect_contract_dates(
        (symbol,),
        start=date(2026, 4, 2),
        end=date(2026, 4, 3),
    )

    assert set(pairs) == {
        ContractDate("HC", "SHFE", "HC2605.SHF", date(2026, 4, 2)),
        ContractDate("HC", "SHFE", "HC2610.SHF", date(2026, 4, 3)),
    }


def test_collect_contract_dates_includes_old_contract_night_session_on_roll(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "HC"
    directory.mkdir()
    pd.DataFrame(
        {
            "datetime": ["2026-04-02 09:01:00", "2026-04-02 21:01:00"],
            "contract_code": ["HC2605.SHF", "HC2605.SHF"],
        }
    ).to_parquet(directory / "2026-04-02.parquet", index=False)
    pd.DataFrame(
        {
            "datetime": ["2026-04-03 09:01:00"],
            "contract_code": ["HC2610.SHF"],
        }
    ).to_parquet(directory / "2026-04-03.parquet", index=False)
    symbol = DiscoveredSymbol(
        root_symbol="HC",
        exchange="SHFE",
        vt_symbol="HC0.SHFE",
        source_directory=directory,
    )

    pairs = collect_contract_dates(
        (symbol,),
        start=date(2026, 4, 2),
        end=date(2026, 4, 3),
    )

    assert set(pairs) == {
        ContractDate("HC", "SHFE", "HC2605.SHF", date(2026, 4, 2)),
        ContractDate("HC", "SHFE", "HC2605.SHF", date(2026, 4, 3)),
        ContractDate("HC", "SHFE", "HC2610.SHF", date(2026, 4, 3)),
    }


def test_collect_contract_dates_includes_old_contract_from_contract_minute_file(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "minute" / "Y"
    directory.mkdir(parents=True)
    contract_directory = tmp_path / "contract" / "Y" / "minute"
    contract_directory.mkdir(parents=True)
    pd.DataFrame(
        {
            "datetime": ["2026-04-03 09:01:00"],
            "contract_code": ["Y2605.DCE"],
        }
    ).to_parquet(directory / "2026-04-03.parquet", index=False)
    pd.DataFrame(
        {
            "datetime": ["2026-04-07 09:00:00", "2026-04-07 09:01:00"],
            "contract_code": ["Y2609.DCE", "Y2609.DCE"],
        }
    ).to_parquet(directory / "2026-04-07.parquet", index=False)
    pd.DataFrame(
        {
            "datetime": ["2026-04-07 09:00:00", "2026-04-07 09:01:00"],
            "contract_code": ["Y2605.DCE", "Y2605.DCE"],
        }
    ).to_parquet(contract_directory / "Y2605_DCE.parquet", index=False)
    symbol = DiscoveredSymbol(
        root_symbol="Y",
        exchange="DCE",
        vt_symbol="Y0.DCE",
        source_directory=directory,
    )

    pairs = collect_contract_dates(
        (symbol,),
        start=date(2026, 4, 3),
        end=date(2026, 4, 7),
    )

    assert set(pairs) == {
        ContractDate("Y", "DCE", "Y2605.DCE", date(2026, 4, 3)),
        ContractDate("Y", "DCE", "Y2605.DCE", date(2026, 4, 7)),
        ContractDate("Y", "DCE", "Y2609.DCE", date(2026, 4, 7)),
    }


def test_night_session_start_excludes_first_trade_date_after_holiday() -> None:
    assert (
        _night_session_start_for_trade_date(
            trade_date=date(2026, 1, 5),
            prior_open_date=date(2025, 12, 31),
            exchange_has_night=True,
        )
        is None
    )
    assert _night_session_start_for_trade_date(
        trade_date=date(2026, 7, 27),
        prior_open_date=date(2026, 7, 24),
        exchange_has_night=True,
    ) == "21:00:00"
    assert _night_session_start_for_trade_date(
        trade_date=date(2026, 7, 28),
        prior_open_date=date(2026, 7, 27),
        exchange_has_night=True,
    ) == "21:00:00"


def test_build_extension_frames_reconciles_exact_contract_day() -> None:
    empty_base = {
        "exchange_calendar.csv": pd.DataFrame(
            {
                "exchange": ["DCE"],
                "exchange_trade_date": ["2026-07-27"],
            }
        ),
        "contract_specs.csv": pd.DataFrame(),
        "contract_daily.csv": pd.DataFrame(),
        "fee_margin_schedule.csv": pd.DataFrame(),
    }
    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 27),
    )

    result = build_extension_frames(
        pairs=(pair,),
        base_frames=empty_base,
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        source_client=_FakeMetadataClient(),
    )

    assert len(result.frames["exchange_calendar.csv"]) == 1
    spec = result.frames["contract_specs.csv"].iloc[0]
    assert spec["contract_code"] == "I2609.DCE"
    assert spec["contract_size"] == 100.0
    assert spec["session_template_id"] == "CN_COMMODITY_NIGHT_2300"
    daily = result.frames["contract_daily.csv"].iloc[0]
    assert daily["pre_settlement"] == 800.0
    assert daily["limit_up"] == 880.0
    assert daily["known_at"] == "2026-07-24T16:00:00+08:00"
    assert daily["pre_settlement_known_at"] == "2026-07-24T15:30:00+08:00"
    assert daily["settlement_known_at"] == "2026-07-27T15:30:00+08:00"
    assert (
        result.frames["fee_margin_schedule.csv"].iloc[0]["known_at"]
        == "2026-07-24T16:00:00+08:00"
    )
    assert len(result.frames["fee_margin_schedule.csv"]) == 1


def test_build_extension_frames_rebuilds_legacy_daily_without_pit_fields() -> None:
    base_frames = {
        "exchange_calendar.csv": pd.DataFrame(),
        "contract_specs.csv": pd.DataFrame(
            {
                "contract_code": ["I2609.DCE"],
                "root_symbol": ["I"],
                "exchange": ["DCE"],
                "contract_size": [100.0],
                "price_tick": [0.5],
                "lot_step": [1],
                "slippage_ticks_base": [1.0],
                "last_trade_date": ["2026-09-14"],
                "session_template_id": ["CN_COMMODITY_NIGHT_2300"],
                "source": ["DCE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC"],
                "known_at": ["2025-09-15T00:00:00+08:00"],
            }
        ),
        "contract_daily.csv": pd.DataFrame(
            {
                "contract_code": ["I2609.DCE"],
                "exchange_trade_date": ["2026-07-27"],
                "pre_settlement": [800.0],
                "known_at": ["2026-07-27T09:00:00+08:00"],
            }
        ),
        "fee_margin_schedule.csv": pd.DataFrame(),
    }

    result = build_extension_frames(
        pairs=(
            ContractDate(
                root_symbol="I",
                exchange="DCE",
                contract_code="I2609.DCE",
                trade_date=date(2026, 7, 27),
            ),
        ),
        base_frames=base_frames,
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        source_client=_FakeMetadataClient(),
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    assert daily["pre_settlement_known_at"] == "2026-07-24T15:30:00+08:00"
    assert daily["contract_size"] == pytest.approx(100.0)
    assert daily["price_tick"] == pytest.approx(0.5)
    assert daily["mechanics_source"] == "DCE_CONTRACT_ARCHIVE_VIA_TUSHARE_FUT_BASIC"


def test_build_extension_frames_uses_causal_fb_historical_fee_snapshot() -> None:
    pair = ContractDate(
        root_symbol="FB",
        exchange="DCE",
        contract_code="FB2609.DCE",
        trade_date=date(2026, 7, 2),
    )

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 2),
        end=date(2026, 7, 2),
        source_client=_FbMissingVendorClient(),
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    fee = result.frames["fee_margin_schedule.csv"].iloc[0]
    assert daily["pre_settlement"] == 1329.0
    assert daily["limit_up"] == 1395.0
    assert daily["limit_down"] == 1263.0
    assert fee["open_fee_rate"] == pytest.approx(0.0001)
    assert fee["close_fee_rate"] == pytest.approx(0.0001)
    assert fee["close_today_fee_rate"] == pytest.approx(0.0001)
    assert fee["margin_rate_long"] == pytest.approx(0.50)
    assert "DCE_FB_HISTORICAL_FEE_MIRROR" in fee["source"]
    assert "dce_fb_fee_20260617.csv" in fee["source_url_or_file"]
    assert any(
        record["kind"] == "checked_in_historical_fee_snapshot"
        for record in result.audit_records
    )


@pytest.mark.parametrize(
    ("trade_date", "expected_open", "expected_close", "expected_close_today"),
    [
        (date(2026, 3, 24), 6.0, 6.0, 6.0),
        (date(2026, 3, 25), 12.0, 6.0, 12.0),
        (date(2026, 3, 26), 12.0, 6.0, 12.0),
    ],
)
def test_build_extension_frames_uses_causal_pg_historical_fee_notice(
    trade_date: date,
    expected_open: float,
    expected_close: float,
    expected_close_today: float,
) -> None:
    pair = ContractDate(
        root_symbol="PG",
        exchange="DCE",
        contract_code="PG2605.DCE",
        trade_date=trade_date,
    )

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=trade_date,
        end=trade_date,
        source_client=_PgMissingVendorClient(),
    )

    fee = result.frames["fee_margin_schedule.csv"].iloc[0]
    assert fee["fee_per_lot_open"] == pytest.approx(expected_open)
    assert fee["fee_per_lot_close"] == pytest.approx(expected_close)
    assert fee["fee_per_lot_close_today"] == pytest.approx(
        expected_close_today
    )
    assert fee["margin_rate_long"] == pytest.approx(0.25)
    assert "DCE_PG_HISTORICAL_FEE_MIRROR" in fee["source"]
    assert "dce_pg_fee_20260323.csv" in fee["source_url_or_file"]
    assert any(
        record["source_key"] == "dce_pg_fee_20260323.csv"
        for record in result.audit_records
    )


def test_build_extension_frames_prefers_exact_pg2607_snapshot_on_scoped_roll(
) -> None:
    result = build_extension_frames(
        pairs=(
            ContractDate(
                root_symbol="PG",
                exchange="DCE",
                contract_code="PG2605.DCE",
                trade_date=date(2026, 4, 14),
            ),
            ContractDate(
                root_symbol="PG",
                exchange="DCE",
                contract_code="PG2607.DCE",
                trade_date=date(2026, 4, 15),
            ),
        ),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 4, 14),
        end=date(2026, 4, 15),
        source_client=_PgScopedFeeRollClient(),
    )

    fee_rows = result.frames["fee_margin_schedule.csv"]
    fee = fee_rows.loc[
        fee_rows["contract_code"].eq("PG2607.DCE")
    ].iloc[0]
    assert fee["fee_per_lot_open"] == pytest.approx(6.0)
    assert fee["fee_per_lot_close"] == pytest.approx(6.0)
    assert fee["fee_per_lot_close_today"] == pytest.approx(6.0)
    assert "DCE_PG_HISTORICAL_FEE_MIRROR" in fee["source"]
    assert "dce_pg_fee_20260323.csv" in fee["source_url_or_file"]
    assert (
        "jin10:futures_comm:20260415:late_contract_validation:PG2607"
        in fee["source_url_or_file"]
    )


def test_build_extension_frames_uses_czce_official_pf_fees() -> None:
    class MissingPfVendorClient(_FakeMetadataClient):
        def fetch_calendar(
            self,
            exchange: str,
            start: date,
            end: date,
        ) -> pd.DataFrame:
            self.calls += 1
            del start, end
            assert exchange == "ZCE"
            return pd.DataFrame(
                {
                    "exchange": ["ZCE"] * 4,
                    "cal_date": [
                        "20260702",
                        "20260703",
                        "20260706",
                        "20260707",
                    ],
                    "is_open": [1, 1, 1, 1],
                    "pretrade_date": [
                        "20260701",
                        "20260702",
                        "20260703",
                        "20260706",
                    ],
                }
            )

        def fetch_contracts(self, exchange: str) -> pd.DataFrame:
            self.calls += 1
            assert exchange == "ZCE"
            return pd.DataFrame(
                {
                    "ts_code": ["PF2609.ZCE"],
                    "exchange": ["ZCE"],
                    "fut_code": ["PF"],
                    "per_unit": [5.0],
                    "quote_unit_desc": ["2人民币元/吨"],
                    "list_date": ["20250915"],
                    "delist_date": ["20260914"],
                    "trade_time_desc": [
                        "上午9:00-11:30 下午1:30-3:00"
                        "及交易所规定的其他交易时间"
                    ],
                }
            )

        def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
            self.calls += 1
            settlement = {
                date(2026, 7, 3): 6792.0,
                date(2026, 7, 6): 6800.0,
            }[trade_date]
            return pd.DataFrame(
                [
                    _settlement_row(
                        ts_code="PF2609.ZCE",
                        trade_date=f"{trade_date:%Y%m%d}",
                        settle=settlement,
                        margin=0.10,
                    )
                ]
            )

        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            self.calls += 1
            assert source_date == date(2026, 7, 3)
            return pd.DataFrame(columns=pd.DataFrame([_vendor_row()]).columns)

        def fetch_czce_settlement_parameters(
            self,
            source_date: date,
        ) -> pd.DataFrame:
            self.calls += 1
            assert source_date == date(2026, 7, 3)
            return _parse_czce_settlement_parameters_text(
                "header\n"
                "header2\n"
                "PF609|6792.00|N|0|10|±9|2.00|绝对值|0.00|0.00|11181|\n",
                source_date,
            )

        def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
            self.calls += 1
            assert source_date == date(2026, 7, 6)
            return pd.DataFrame(
                {
                    "生效日期": [source_date.isoformat()],
                    "交易所": ["郑商所"],
                    "代码": ["PF"],
                    "交易保证金比例": [15.0],
                    "涨跌停板幅度": [9.0],
                    "合约乘数": [5.0],
                    "最小变动价位": [2.0],
                    "特殊合约参数调整": [None],
                }
            )

    result = build_extension_frames(
        pairs=(
            ContractDate(
                root_symbol="PF",
                exchange="ZCE",
                contract_code="PF2609.ZCE",
                trade_date=date(2026, 7, 6),
            ),
        ),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 6),
        end=date(2026, 7, 6),
        source_client=MissingPfVendorClient(),
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    fee = result.frames["fee_margin_schedule.csv"].iloc[0]
    assert daily["limit_up"] == pytest.approx(7404.0)
    assert daily["limit_down"] == pytest.approx(6180.0)
    assert fee["fee_per_lot_open"] == pytest.approx(2.0)
    assert fee["fee_per_lot_close"] == pytest.approx(2.0)
    assert fee["fee_per_lot_close_today"] == pytest.approx(0.0)
    assert fee["margin_rate_long"] == pytest.approx(0.15)
    assert "CZCE_OFFICIAL_TUSHARE" in fee["source"]
    assert "czce:FutureDataClearParams:20260703" in fee["source_url_or_file"]


def test_build_extension_frames_uses_czce_official_pf_when_jin10_is_late(
) -> None:
    class LatePfVendorClient(_FakeMetadataClient):
        def __init__(self) -> None:
            super().__init__()
            self.vendor_dates: list[date] = []

        def fetch_calendar(
            self,
            exchange: str,
            start: date,
            end: date,
        ) -> pd.DataFrame:
            self.calls += 1
            del start, end
            assert exchange == "ZCE"
            return pd.DataFrame(
                {
                    "exchange": ["ZCE", "ZCE", "ZCE"],
                    "cal_date": ["20260713", "20260714", "20260715"],
                    "is_open": [1, 1, 1],
                    "pretrade_date": ["20260710", "20260713", "20260714"],
                }
            )

        def fetch_contracts(self, exchange: str) -> pd.DataFrame:
            self.calls += 1
            assert exchange == "ZCE"
            return pd.DataFrame(
                {
                    "ts_code": ["PF2609.ZCE"],
                    "exchange": ["ZCE"],
                    "fut_code": ["PF"],
                    "per_unit": [5.0],
                    "quote_unit_desc": ["2人民币元/吨"],
                    "list_date": ["20250915"],
                    "delist_date": ["20260914"],
                    "trade_time_desc": [
                        "上午9:00-11:30 下午1:30-3:00"
                        "及交易所规定的其他交易时间"
                    ],
                }
            )

        def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
            self.calls += 1
            settlement = {
                date(2026, 7, 13): 7104.0,
                date(2026, 7, 14): 7300.0,
            }[trade_date]
            return pd.DataFrame(
                [
                    _settlement_row(
                        ts_code="PF2609.ZCE",
                        trade_date=f"{trade_date:%Y%m%d}",
                        settle=settlement,
                        margin=0.10,
                    )
                ]
            )

        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            self.calls += 1
            self.vendor_dates.append(source_date)
            if source_date != date(2026, 7, 13):
                return pd.DataFrame(columns=pd.DataFrame([_vendor_row()]).columns)
            return pd.DataFrame(
                [
                    {
                        **_vendor_row(),
                        "日期": "20260713",
                        "合约品种": "短纤609",
                        "合约代码": "PF609",
                        "手续费公布时间": "2026-07-13 23:40:44",
                        "价格公布时间": "2026-07-13 23:57:26",
                        "现价": 7104.0,
                        "涨停板": 7744.0,
                        "跌停板": 6464.0,
                        "保证金/买开": "10%",
                        "保证金/卖开": "10%",
                        "开仓": "2元",
                        "平昨": "2元",
                        "平今": "0元",
                    }
                ]
            )

        def fetch_czce_settlement_parameters(
            self,
            source_date: date,
        ) -> pd.DataFrame:
            self.calls += 1
            assert source_date == date(2026, 7, 13)
            return _parse_czce_settlement_parameters_text(
                "header\n"
                "header2\n"
                "PF609|7,104.00|N|0|10|±9|2.00|绝对值|0.00|0.00|15550|\n",
                source_date,
            )

        def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
            self.calls += 1
            assert source_date == date(2026, 7, 14)
            return pd.DataFrame(
                {
                    "生效日期": [source_date.isoformat()],
                    "交易所": ["郑商所"],
                    "代码": ["PF"],
                    "交易保证金比例": [10.0],
                    "涨跌停板幅度": [9.0],
                    "合约乘数": [5.0],
                    "最小变动价位": [2.0],
                    "特殊合约参数调整": [None],
                }
            )

    client = LatePfVendorClient()
    result = build_extension_frames(
        pairs=(
            ContractDate(
                root_symbol="PF",
                exchange="ZCE",
                contract_code="PF2609.ZCE",
                trade_date=date(2026, 7, 14),
            ),
        ),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 14),
        end=date(2026, 7, 14),
        source_client=client,
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    fee = result.frames["fee_margin_schedule.csv"].iloc[0]
    assert client.vendor_dates == [date(2026, 7, 13)]
    assert daily["limit_up"] == pytest.approx(7744.0)
    assert daily["limit_down"] == pytest.approx(6464.0)
    assert fee["fee_per_lot_open"] == pytest.approx(2.0)
    assert fee["fee_per_lot_close"] == pytest.approx(2.0)
    assert fee["fee_per_lot_close_today"] == pytest.approx(0.0)
    assert "CZCE_OFFICIAL_TUSHARE" in fee["source"]


def test_build_extension_frames_validates_dce_roll_from_target_close() -> None:
    pairs = (
        ContractDate(
            root_symbol="JD",
            exchange="DCE",
            contract_code="JD2604.DCE",
            trade_date=date(2026, 3, 4),
        ),
        ContractDate(
            root_symbol="JD",
            exchange="DCE",
            contract_code="JD2605.DCE",
            trade_date=date(2026, 3, 5),
        ),
    )

    result = build_extension_frames(
        pairs=pairs,
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 3, 4),
        end=date(2026, 3, 5),
        source_client=_DceTargetCloseRollClient(),
    )

    daily = result.frames["contract_daily.csv"].loc[
        lambda frame: frame["contract_code"].eq("JD2605.DCE")
    ].iloc[0]
    fee = result.frames["fee_margin_schedule.csv"].loc[
        lambda frame: frame["contract_code"].eq("JD2605.DCE")
    ].iloc[0]
    assert daily["pre_settlement"] == 3353.0
    assert daily["limit_up"] == 3554.0
    assert daily["limit_down"] == 3152.0
    assert fee["open_fee_rate"] == pytest.approx(0.00015)
    assert fee["close_fee_rate"] == pytest.approx(0.00015)
    assert fee["close_today_fee_rate"] == pytest.approx(0.00015)
    assert fee["margin_rate_long"] == pytest.approx(0.13)
    assert "ROLL_ENTRY_VALIDATED" in fee["source"]
    assert "late_contract_validation" in fee["source_url_or_file"]


def test_dce_target_close_roll_uses_earlier_causal_fee_reference() -> None:
    class LatePriorFeeClient(_DceTargetCloseRollClient):
        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            frame = super().fetch_vendor_parameters(source_date)
            if source_date == date(2026, 3, 4):
                frame.loc[:, "手续费公布时间"] = "2026-03-05 10:00:00"
            elif source_date == date(2026, 3, 5):
                reference = frame["合约代码"].eq("jd2604")
                frame.loc[reference, "手续费公布时间"] = (
                    "2026-03-05 10:00:00"
                )
            return frame

    pairs = (
        ContractDate(
            root_symbol="JD",
            exchange="DCE",
            contract_code="JD2604.DCE",
            trade_date=date(2026, 3, 4),
        ),
        ContractDate(
            root_symbol="JD",
            exchange="DCE",
            contract_code="JD2605.DCE",
            trade_date=date(2026, 3, 5),
        ),
    )

    result = build_extension_frames(
        pairs=pairs,
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 3, 4),
        end=date(2026, 3, 5),
        source_client=LatePriorFeeClient(),
    )

    fee = result.frames["fee_margin_schedule.csv"].loc[
        lambda frame: frame["contract_code"].eq("JD2605.DCE")
    ].iloc[0]
    assert fee["open_fee_rate"] == pytest.approx(0.00015)
    assert "jd2604@20260303" in fee["source_url_or_file"]


def test_dce_roll_searches_multiple_prior_snapshots_before_assuming() -> None:
    class OlderExactFeeClient(_DceTargetCloseRollClient):
        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            if source_date == date(2026, 3, 2):
                self.calls += 1
                return pd.DataFrame(
                    [
                        self._vendor_row(
                            source_date=source_date,
                            contract_code="jd2605",
                            settlement=3353.0,
                            fee_known_at="2026-03-02 16:00:00",
                            price_known_at="2026-03-02 16:00:00",
                        )
                    ]
                )
            if source_date in {date(2026, 3, 3), date(2026, 3, 4)}:
                self.calls += 1
                return pd.DataFrame(columns=pd.DataFrame([_vendor_row()]).columns)
            return super().fetch_vendor_parameters(source_date).loc[
                lambda frame: frame["合约代码"].eq("jd2605")
            ]

        def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
            if source_date == date(2026, 3, 3):
                self.calls += 1
                return pd.DataFrame(
                    {
                        "生效日期": [source_date.isoformat()],
                        "交易所": ["大商所"],
                        "代码": ["JD"],
                        "交易保证金比例": [13.0],
                        "涨跌停板幅度": [6.0],
                        "合约乘数": [10.0],
                        "最小变动价位": [1.0],
                        "特殊合约参数调整": [None],
                    }
                )
            return super().fetch_trading_rules(source_date)

    base = build_extension_frames(
        pairs=(ContractDate("JD", "DCE", "JD2604.DCE", date(2026, 3, 4)),),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 3, 4),
        end=date(2026, 3, 4),
        source_client=_DceTargetCloseRollClient(),
    ).frames
    result = build_extension_frames(
        pairs=(
            ContractDate("JD", "DCE", "JD2604.DCE", date(2026, 3, 4)),
            ContractDate("JD", "DCE", "JD2605.DCE", date(2026, 3, 5)),
        ),
        base_frames=base,
        start=date(2026, 3, 4),
        end=date(2026, 3, 5),
        source_client=OlderExactFeeClient(),
        allow_runtime_defaults=True,
    )

    fee = result.frames["fee_margin_schedule.csv"].loc[
        lambda frame: frame["contract_code"].eq("JD2605.DCE")
    ].iloc[0]
    assert "jd2605@20260302" in fee["source_url_or_file"]
    assert "ASSUMED_RUNTIME_DEFAULT" not in fee["source"]
    assert result.assumptions == ()


def test_dce_roll_runtime_default_is_opt_in_and_audited() -> None:
    class MissingFeeReferenceClient(_DceTargetCloseRollClient):
        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            if source_date <= date(2026, 3, 4):
                self.calls += 1
                return pd.DataFrame(columns=pd.DataFrame([_vendor_row()]).columns)
            return super().fetch_vendor_parameters(source_date).loc[
                lambda frame: frame["合约代码"].eq("jd2605")
            ]

    pairs = (
        ContractDate("JD", "DCE", "JD2604.DCE", date(2026, 3, 4)),
        ContractDate("JD", "DCE", "JD2605.DCE", date(2026, 3, 5)),
    )
    base_frames = build_extension_frames(
        pairs=(ContractDate("JD", "DCE", "JD2604.DCE", date(2026, 3, 4)),),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 3, 4),
        end=date(2026, 3, 4),
        source_client=_DceTargetCloseRollClient(),
    ).frames

    with pytest.raises(MetadataBuildError, match="MISSING_ROLL_FEE_REFERENCE"):
        build_extension_frames(
            pairs=pairs,
            base_frames=base_frames,
            start=date(2026, 3, 4),
            end=date(2026, 3, 5),
            source_client=MissingFeeReferenceClient(),
        )

    result = build_extension_frames(
        pairs=pairs,
        base_frames=base_frames,
        start=date(2026, 3, 4),
        end=date(2026, 3, 5),
        source_client=MissingFeeReferenceClient(),
        allow_runtime_defaults=True,
    )

    fee = result.frames["fee_margin_schedule.csv"].loc[
        lambda frame: frame["contract_code"].eq("JD2605.DCE")
    ].iloc[0]
    assert fee["open_fee_rate"] == pytest.approx(0.00015)
    assert "ASSUMED_RUNTIME_DEFAULT" in fee["source"]
    assert result.assumptions == (
        {
            "root_symbol": "JD",
            "contract_code": "JD2605.DCE",
            "exchange_trade_date": "2026-03-05",
            "field": "roll_fee_reference",
            "reason_code": "MISSING_ROLL_FEE_REFERENCE",
            "fallback": "CURRENT_CONTRACT_RUNTIME_PARAMETERS",
        },
    )


def test_dce_post_roll_night_uses_visible_old_contract_fee_reference() -> None:
    class LateNewContractFeeClient(_DceTargetCloseRollClient):
        def fetch_contracts(self, exchange: str) -> pd.DataFrame:
            frame = super().fetch_contracts(exchange)
            frame.loc[:, "trade_time_desc"] = (
                "日盘交易：9:00-11:30，13:30-15:00；"
                "夜盘交易：21:00至23:00"
            )
            return frame

        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            frame = super().fetch_vendor_parameters(source_date)
            if source_date == date(2026, 3, 5):
                target = frame["合约代码"].eq("jd2605")
                frame.loc[target, "手续费公布时间"] = (
                    "2026-03-05 22:00:00"
                )
            return frame

    pairs = (
        ContractDate(
            root_symbol="JD",
            exchange="DCE",
            contract_code="JD2604.DCE",
            trade_date=date(2026, 3, 4),
        ),
        ContractDate(
            root_symbol="JD",
            exchange="DCE",
            contract_code="JD2605.DCE",
            trade_date=date(2026, 3, 5),
        ),
        ContractDate(
            root_symbol="JD",
            exchange="DCE",
            contract_code="JD2605.DCE",
            trade_date=date(2026, 3, 6),
        ),
    )

    result = build_extension_frames(
        pairs=pairs,
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 3, 4),
        end=date(2026, 3, 6),
        source_client=LateNewContractFeeClient(),
    )

    fee = result.frames["fee_margin_schedule.csv"].loc[
        lambda frame: frame["contract_code"].eq("JD2605.DCE")
        & frame["source"].str.contains("POST_ROLL_NIGHT_FEE_VALIDATED")
    ].iloc[0]
    assert fee["open_fee_rate"] == pytest.approx(0.00015)
    assert "jd2604@20260304" in fee["source_url_or_file"]
    assert "late_contract_validation:JD2605.DCE" in fee["source_url_or_file"]


def test_dce_missing_vendor_row_does_not_masquerade_as_post_roll() -> None:
    class StableContractClient(_DceTargetCloseRollClient):
        def fetch_contracts(self, exchange: str) -> pd.DataFrame:
            frame = super().fetch_contracts(exchange)
            frame.loc[:, "trade_time_desc"] = (
                "日盘交易：9:00-11:30，13:30-15:00；"
                "夜盘交易：21:00至23:00"
            )
            return frame

        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            frame = super().fetch_vendor_parameters(source_date)
            if source_date == date(2026, 3, 5):
                target = frame["合约代码"].eq("jd2605")
                frame.loc[target, "手续费公布时间"] = (
                    "2026-03-05 22:00:00"
                )
            elif source_date == date(2026, 3, 3):
                visible = self._vendor_row(
                    source_date=source_date,
                    contract_code="jd2605",
                    settlement=3388.0,
                    fee_known_at="2026-03-03 16:00:00",
                    price_known_at="2026-03-03 16:00:00",
                )
                frame = pd.concat([frame, pd.DataFrame([visible])])
            return frame

    result = build_extension_frames(
        pairs=(
            ContractDate(
                root_symbol="JD",
                exchange="DCE",
                contract_code="JD2605.DCE",
                trade_date=date(2026, 3, 6),
            ),
        ),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 3, 6),
        end=date(2026, 3, 6),
        source_client=StableContractClient(),
    )

    fee = result.frames["fee_margin_schedule.csv"].iloc[0]
    assert "POST_ROLL_NIGHT_FEE_VALIDATED" not in fee["source"]
    assert "jin10:futures_comm:20260303" in fee["source_url_or_file"]


def test_build_extension_frames_loads_exact_gtja_margin_rule() -> None:
    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 27),
    )
    client = _ExactMarginRuleClient()

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        source_client=client,
    )

    fee = result.frames["fee_margin_schedule.csv"].iloc[0]
    assert client.rule_dates == [date(2026, 7, 27)]
    assert fee["margin_rate_long"] == pytest.approx(0.15)
    assert fee["margin_rate_short"] == pytest.approx(0.15)
    assert "GTJA_COMPANY_MARGIN" in fee["source"]


def test_build_extension_frames_roll_uses_daily_settlement_and_gtja_margin(
) -> None:
    pairs = (
        ContractDate(
            root_symbol="I",
            exchange="DCE",
            contract_code="I2608.DCE",
            trade_date=date(2026, 7, 27),
        ),
        ContractDate(
            root_symbol="I",
            exchange="DCE",
            contract_code="I2609.DCE",
            trade_date=date(2026, 7, 28),
        ),
    )

    result = build_extension_frames(
        pairs=pairs,
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 27),
        end=date(2026, 7, 28),
        source_client=_RollDailyMarginClient(),
    )

    fee = result.frames["fee_margin_schedule.csv"].loc[
        lambda frame: frame["contract_code"].eq("I2609.DCE")
    ].iloc[0]
    assert fee["margin_rate_long"] == pytest.approx(0.15)
    assert fee["known_at"] == "2026-07-27T21:00:00+08:00"
    assert "ROLL_ENTRY_VALIDATED" in fee["source"]
    assert "GTJA_COMPANY_MARGIN" in fee["source"]


def test_build_extension_frames_fetches_dated_rules_only_for_stale_vendor_price(
) -> None:
    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 27),
    )
    client = _RebasedSettlementClient()

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        source_client=client,
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    assert client.rule_dates == [date(2026, 7, 27)]
    assert daily["limit_up"] == 878.5
    assert daily["limit_down"] == 719.5
    assert daily["limit_rounding_rule"] == (
        "GTJA_EFFECTIVE_RATE_JIN10_"
        "DCE_PUBLISHED_INTERVAL_FLOOR_AMPLITUDE_REBASED"
    )


def test_build_extension_frames_validates_missing_vendor_rule_snapshot() -> None:
    class MissingSnapshotClient(_RebasedSettlementClient):
        def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
            if source_date == date(2026, 7, 27):
                self.calls += 1
                self.rule_dates.append(source_date)
                raise MetadataBuildError(
                    "MISSING_TRADE_RULE_SNAPSHOT",
                    source_date.isoformat(),
                )
            return super().fetch_trading_rules(source_date)

    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 27),
    )
    client = MissingSnapshotClient()

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        source_client=client,
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    assert client.rule_dates == [date(2026, 7, 27), date(2026, 7, 24)]
    assert daily["limit_rounding_rule"].endswith("CARRY_FORWARD_VALIDATED")


def test_build_extension_frames_uses_latest_parameters_visible_before_open() -> None:
    class LateClient(_RebasedSettlementClient):
        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            if source_date == date(2026, 7, 24):
                return super().fetch_vendor_parameters(source_date)
            self.calls += 1
            assert source_date == date(2026, 7, 27)
            row = _vendor_row()
            row.update(
                {
                    "日期": "20260727",
                    "现价": 805.0,
                    "涨停板": 885.5,
                    "跌停板": 724.5,
                    "手续费公布时间": "2026-07-27 16:00:00",
                    "价格公布时间": "2026-07-27 21:01:00",
                }
            )
            return pd.DataFrame([row])

    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 28),
    )

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 28),
        end=date(2026, 7, 28),
        source_client=LateClient(),
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    fee = result.frames["fee_margin_schedule.csv"].iloc[0]
    assert daily["known_at"] == "2026-07-24T16:00:00+08:00"
    assert fee["known_at"] == "2026-07-27T15:30:00+08:00"


def test_build_extension_frames_uses_latest_snapshot_containing_contract() -> None:
    class MissingLatestClient(_RebasedSettlementClient):
        def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
            if source_date == date(2026, 7, 24):
                return super().fetch_vendor_parameters(source_date)
            self.calls += 1
            assert source_date == date(2026, 7, 27)
            row = _vendor_row()
            row.update(
                {
                    "日期": "20260727",
                    "合约代码": "J2609",
                    "手续费公布时间": "2026-07-27 16:00:00",
                    "价格公布时间": "2026-07-27 16:00:00",
                }
            )
            return pd.DataFrame([row])

    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 28),
    )

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 28),
        end=date(2026, 7, 28),
        source_client=MissingLatestClient(),
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    assert daily["known_at"] == "2026-07-24T16:00:00+08:00"


def test_build_extension_frames_uses_exact_daily_quote_for_missing_settlement(
) -> None:
    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 27),
    )

    result = build_extension_frames(
        pairs=(pair,),
        base_frames={
            filename: pd.DataFrame()
            for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )
        },
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        source_client=_DailySettlementFallbackClient(),
    )

    daily = result.frames["contract_daily.csv"].iloc[0]
    assert daily["pre_settlement"] == 800.0
    assert daily["settlement"] == 805.0
    assert "TUSHARE_FUT_DAILY" in daily["source"]


def test_build_extension_frames_rejects_daily_quote_pre_settlement_mismatch(
) -> None:
    pair = ContractDate(
        root_symbol="I",
        exchange="DCE",
        contract_code="I2609.DCE",
        trade_date=date(2026, 7, 27),
    )

    with pytest.raises(MetadataBuildError, match="DAILY_PRE_SETTLEMENT_MISMATCH"):
        build_extension_frames(
            pairs=(pair,),
            base_frames={
                filename: pd.DataFrame()
                for filename in (
                    "exchange_calendar.csv",
                    "contract_specs.csv",
                    "contract_daily.csv",
                    "fee_margin_schedule.csv",
                )
            },
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            source_client=_MismatchedDailyPreSettlementClient(),
        )


def test_build_extension_frames_checks_every_contract_lifecycle_date() -> None:
    class ExpiredClient(_FakeMetadataClient):
        def fetch_contracts(self, exchange: str) -> pd.DataFrame:
            frame = super().fetch_contracts(exchange)
            frame.loc[:, "delist_date"] = "20260727"
            return frame

    pairs = tuple(
        ContractDate(
            root_symbol="I",
            exchange="DCE",
            contract_code="I2609.DCE",
            trade_date=trade_date,
        )
        for trade_date in (date(2026, 7, 27), date(2026, 7, 28))
    )

    with pytest.raises(MetadataBuildError, match="CONTRACT_LIFECYCLE_MISMATCH"):
        build_extension_frames(
            pairs=pairs,
            base_frames={filename: pd.DataFrame() for filename in (
                "exchange_calendar.csv",
                "contract_specs.csv",
                "contract_daily.csv",
                "fee_margin_schedule.csv",
            )},
            start=date(2026, 7, 27),
            end=date(2026, 7, 28),
            source_client=ExpiredClient(),
        )


def test_prepare_execution_metadata_publishes_and_reuses_verified_cache(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "minute" / "I"
    directory.mkdir(parents=True)
    pd.DataFrame(
        {
            "datetime": ["2026-07-27 09:01:00"],
            "contract_code": ["I2609.DCE"],
        }
    ).to_parquet(directory / "2026-07-27.parquet", index=False)
    symbol = DiscoveredSymbol(
        root_symbol="I",
        exchange="DCE",
        vt_symbol="I0.DCE",
        source_directory=directory,
    )
    base_root = Path("cta/strategy/brooks/scalp/meta")
    cache_root = tmp_path / "metadata_cache"
    first_client = _FakeMetadataClient()

    first = prepare_execution_metadata(
        symbols=(symbol,),
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        base_root=base_root,
        cache_root=cache_root,
        source_client=first_client,
    )

    assert not first.cache_hit
    assert first_client.calls == 5
    assert (first.metadata_root / "manifest.json").is_file()
    assert (first.metadata_root / "vendor_source_audit.jsonl.gz").is_file()
    assert first.generated_daily_rows == 1

    second_client = _FakeMetadataClient()
    second = prepare_execution_metadata(
        symbols=(symbol,),
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        base_root=base_root,
        cache_root=cache_root,
        source_client=second_client,
    )

    assert second.cache_hit
    assert second.metadata_root == first.metadata_root
    assert second_client.calls == 0


def test_prepare_execution_metadata_does_not_publish_partial_cache(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "minute" / "I"
    directory.mkdir(parents=True)
    pd.DataFrame(
        {
            "datetime": ["2026-07-27 09:01:00"],
            "contract_code": ["I2609.DCE"],
        }
    ).to_parquet(directory / "2026-07-27.parquet", index=False)
    symbol = DiscoveredSymbol(
        root_symbol="I",
        exchange="DCE",
        vt_symbol="I0.DCE",
        source_directory=directory,
    )
    cache_root = tmp_path / "metadata_cache"

    with pytest.raises(MetadataBuildError, match="SETTLEMENT_MISMATCH"):
        prepare_execution_metadata(
            symbols=(symbol,),
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            base_root="cta/strategy/brooks/scalp/meta",
            cache_root=cache_root,
            source_client=_MismatchedSettlementClient(),
        )

    assert not cache_root.exists() or not any(cache_root.iterdir())


def test_prepare_execution_metadata_rejects_tampered_source_audit(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "minute" / "I"
    directory.mkdir(parents=True)
    pd.DataFrame(
        {
            "datetime": ["2026-07-27 09:01:00"],
            "contract_code": ["I2609.DCE"],
        }
    ).to_parquet(directory / "2026-07-27.parquet", index=False)
    symbol = DiscoveredSymbol(
        root_symbol="I",
        exchange="DCE",
        vt_symbol="I0.DCE",
        source_directory=directory,
    )
    cache_root = tmp_path / "metadata_cache"
    first = prepare_execution_metadata(
        symbols=(symbol,),
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        base_root="cta/strategy/brooks/scalp/meta",
        cache_root=cache_root,
        source_client=_FakeMetadataClient(),
    )
    with (first.metadata_root / "vendor_source_audit.jsonl.gz").open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(MetadataBuildError, match="INVALID_METADATA_CACHE"):
        prepare_execution_metadata(
            symbols=(symbol,),
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            base_root="cta/strategy/brooks/scalp/meta",
            cache_root=cache_root,
            source_client=_FakeMetadataClient(),
        )
