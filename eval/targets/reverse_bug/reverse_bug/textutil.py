"""Text helpers — intentional reverse_words bug."""


def reverse_words(text: str) -> str:
    """Reverse the order of whitespace-separated words.

    BUG: reverses characters of the whole string instead of word order.
    """
    # BUG: should be " ".join(text.split()[::-1])
    return text[::-1]
