"""Tests for clamp_bug.mathutil — fails until clamp is fixed."""

from clamp_bug.mathutil import clamp


def test_clamp_within_range() -> None:
    assert clamp(5, 0, 10) == 5


def test_clamp_above_high() -> None:
    assert clamp(15, 0, 10) == 10


def test_clamp_below_low() -> None:
    assert clamp(-5, 0, 10) == 0
