"""Stats helpers — intentional percentage bug."""


def percentage(part: float, whole: float) -> float:
    """Return ``part`` as a percentage of ``whole`` (0–100 scale).

    BUG: multiplies by 1 instead of 100, so 1/4 returns 0.25 not 25.0.
    """
    if whole == 0:
        raise ZeroDivisionError("whole must be non-zero")
    # BUG: should be (part / whole) * 100
    return (part / whole) * 1
