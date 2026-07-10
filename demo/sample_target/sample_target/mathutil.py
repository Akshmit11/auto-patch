"""Small math helpers.

Bug (demo): ``clamp`` incorrectly uses ``min``/``max`` swapped when
``value`` is below ``low``, so values below the lower bound are not raised.
"""


def add(a: int, b: int) -> int:
    """Return the sum of ``a`` and ``b``."""
    return a + b


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive range [low, high].

    Intentional bug for AutoPatch Day-1: lower bound is not applied correctly.
    """
    if high < low:
        low, high = high, low
    # BUG: should be max(low, min(high, value))
    return min(low, max(high, value))
