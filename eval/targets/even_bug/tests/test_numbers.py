from even_bug.numbers import is_even


def test_is_even_zero() -> None:
    assert is_even(0) is True


def test_is_even_two() -> None:
    assert is_even(2) is True


def test_is_even_three() -> None:
    assert is_even(3) is False
