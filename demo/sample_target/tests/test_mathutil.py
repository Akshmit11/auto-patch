"""Tests for sample_target.mathutil — one fails until clamp is fixed."""

from sample_target.mathutil import add, clamp


def test_add() -> None:
    assert add(2, 3) == 5


def test_clamp_within_range() -> None:
    assert clamp(5, 0, 10) == 5


def test_clamp_above_high() -> None:
    assert clamp(15, 0, 10) == 10


def test_clamp_below_low() -> None:
    """This assertion fails on the buggy implementation (Day-1 demo issue)."""
    assert clamp(-5, 0, 10) == 0
