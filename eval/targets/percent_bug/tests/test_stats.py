import pytest
from percent_bug.stats import percentage


def test_percentage_quarter() -> None:
    assert percentage(1, 4) == 25.0


def test_percentage_half() -> None:
    assert percentage(50, 100) == 50.0


def test_percentage_zero_whole() -> None:
    with pytest.raises(ZeroDivisionError):
        percentage(1, 0)
