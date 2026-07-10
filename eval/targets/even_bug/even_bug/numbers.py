"""Number helpers — intentional parity bug."""


def is_even(n: int) -> bool:
    """Return True if ``n`` is even.

    BUG: returns True for odd numbers instead of even.
    """
    # BUG: should be (n % 2 == 0)
    return n % 2 == 1
