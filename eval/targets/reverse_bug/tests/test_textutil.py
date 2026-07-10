from reverse_bug.textutil import reverse_words


def test_reverse_two_words() -> None:
    assert reverse_words("hello world") == "world hello"


def test_reverse_single() -> None:
    assert reverse_words("solo") == "solo"


def test_reverse_three() -> None:
    assert reverse_words("a b c") == "c b a"
