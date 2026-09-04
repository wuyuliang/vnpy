"""Default sample universe for the stock sample pipeline."""
from __future__ import annotations


DEFAULT_SAMPLE_UNIVERSE: list[dict[str, str]] = [
    {"ts_code": "600519.SH", "exchange": "SSE", "name": "贵州茅台"},
    {"ts_code": "600036.SH", "exchange": "SSE", "name": "招商银行"},
    {"ts_code": "601318.SH", "exchange": "SSE", "name": "中国平安"},
    {"ts_code": "600900.SH", "exchange": "SSE", "name": "长江电力"},
    {"ts_code": "601398.SH", "exchange": "SSE", "name": "工商银行"},
    {"ts_code": "601166.SH", "exchange": "SSE", "name": "兴业银行"},
    {"ts_code": "600276.SH", "exchange": "SSE", "name": "恒瑞医药"},
    {"ts_code": "601288.SH", "exchange": "SSE", "name": "农业银行"},
    {"ts_code": "601857.SH", "exchange": "SSE", "name": "中国石油"},
    {"ts_code": "600030.SH", "exchange": "SSE", "name": "中信证券"},
    {"ts_code": "000001.SZ", "exchange": "SZSE", "name": "平安银行"},
    {"ts_code": "000333.SZ", "exchange": "SZSE", "name": "美的集团"},
    {"ts_code": "000651.SZ", "exchange": "SZSE", "name": "格力电器"},
    {"ts_code": "000858.SZ", "exchange": "SZSE", "name": "五粮液"},
    {"ts_code": "002594.SZ", "exchange": "SZSE", "name": "比亚迪"},
    {"ts_code": "300750.SZ", "exchange": "SZSE", "name": "宁德时代"},
    {"ts_code": "300059.SZ", "exchange": "SZSE", "name": "东方财富"},
    {"ts_code": "002415.SZ", "exchange": "SZSE", "name": "海康威视"},
    {"ts_code": "000725.SZ", "exchange": "SZSE", "name": "京东方A"},
    {"ts_code": "002714.SZ", "exchange": "SZSE", "name": "牧原股份"},
]


def get_default_sample_universe() -> list[dict[str, str]]:
    """Return the default 20-name sample universe."""
    return [dict(item) for item in DEFAULT_SAMPLE_UNIVERSE]


__all__ = ["DEFAULT_SAMPLE_UNIVERSE", "get_default_sample_universe"]

