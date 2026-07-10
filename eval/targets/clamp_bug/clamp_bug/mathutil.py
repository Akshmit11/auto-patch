"""Math helpers — intentional lower-bound bug in clamp()."""


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive range [low, high].

    BUG: lower bound is not applied correctly.
    """
    if high < low:
        low, high = high, low
    # BUG: should be max(low, min(high, value))
    return min(low, max(high, value))
