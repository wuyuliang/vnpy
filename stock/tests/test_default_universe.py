from stock.config.default_universe import DEFAULT_SAMPLE_UNIVERSE, get_default_sample_universe


def test_default_sample_universe_has_20_symbols_split_between_sse_and_szse() -> None:
    universe = get_default_sample_universe()

    assert universe == DEFAULT_SAMPLE_UNIVERSE
    assert len(universe) == 20

    sse = [item for item in universe if item["exchange"] == "SSE"]
    szse = [item for item in universe if item["exchange"] == "SZSE"]

    assert len(sse) == 10
    assert len(szse) == 10
    assert all(str(item["ts_code"]).endswith(".SH") for item in sse)
    assert all(str(item["ts_code"]).endswith(".SZ") for item in szse)

