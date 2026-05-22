from __future__ import annotations

from cta.utils.limit_move import is_limit_move_blocked_by_flags, is_limit_move_blocked_by_ohlc


def test_limit_move_flags_and_ohlc_consistency_for_long() -> None:
    by_ohlc = is_limit_move_blocked_by_ohlc(
        prev_close=100.0,
        next_open=108.0,
        next_high=108.0,
        next_low=108.0,
        side="long",
        limit_move_pct=0.07,
    )
    by_flags = is_limit_move_blocked_by_flags(
        side="long",
        is_limit_up_close=True,
        is_limit_down_close=False,
    )
    assert by_ohlc is True
    assert by_flags is True


def test_limit_move_flags_and_ohlc_consistency_for_short() -> None:
    by_ohlc = is_limit_move_blocked_by_ohlc(
        prev_close=100.0,
        next_open=92.0,
        next_high=92.0,
        next_low=92.0,
        side="short",
        limit_move_pct=0.07,
    )
    by_flags = is_limit_move_blocked_by_flags(
        side="short",
        is_limit_up_close=False,
        is_limit_down_close=True,
    )
    assert by_ohlc is True
    assert by_flags is True
