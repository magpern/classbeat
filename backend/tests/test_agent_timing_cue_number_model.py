"""``timing_cue_number_model.number_tokens``: every number from 0 to 100 is one token in English,
Swedish and Danish, compounds included, and so are digits in any script. Pure, table-driven; the
spellings are written here independently of the module."""

import pytest

from classbeat.agent.timing_cue_number_model import AMBIGUOUS, number_tokens

_EN = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
_EN += "fifteen sixteen seventeen eighteen nineteen"
_EN_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SV = "noll ett två tre fyra fem sex sju åtta nio tio elva tolv tretton fjorton femton sexton "
_SV += "sjutton arton nitton"
_SV_TENS = ["tjugo", "trettio", "fyrtio", "femtio", "sextio", "sjuttio", "åttio", "nittio"]
_DA = "nul en to tre fire fem seks syv otte ni ti elleve tolv tretten fjorten femten seksten "
_DA += "sytten atten nitten"
_DA_TENS = ["tyve", "tredive", "fyrre", "halvtreds", "tres", "halvfjerds", "firs", "halvfems"]


def spell(lang: str, n: int) -> str:
    """``n`` (0 to 100) as a word in ``lang``."""
    small, tens = {"en": (_EN, _EN_TENS), "sv": (_SV, _SV_TENS), "da": (_DA, _DA_TENS)}[lang]
    words = small.split()
    if n < 20:
        return words[n]
    if n == 100:
        return {"en": "one hundred", "sv": "hundra", "da": "hundrede"}[lang]
    ten, unit = tens[n // 10 - 2], n % 10
    if not unit:
        return ten
    return {
        "en": f"{ten}-{words[unit]}",
        "sv": f"{ten}{words[unit]}",
        "da": f"{words[unit]}og{ten}",
    }[lang]


FULL_WIDTH = str.maketrans("0123456789", "".join(chr(0xFF10 + i) for i in range(10)))
LANGS = ("en", "sv", "da")


@pytest.mark.parametrize("lang", LANGS)
def test_every_number_word_to_a_hundred_is_one_token(lang: str) -> None:
    wrong = []
    for n in range(101):
        word = spell(lang, n)
        tokens = number_tokens(f"x {word} y")
        if [(t.value, t.end - t.start) for t in tokens] != [(n, len(word))]:
            wrong.append(word)
    assert wrong == []


def test_compounds_have_their_value() -> None:
    texts = {"tjugofem": 25, "tjugoen": 21, "trettiotvå": 32, "femogtyve": 25, "enogtyve": 21}
    texts |= {"twenty five": 25, "twentyfive": 25, "nittionio": 99, "niogtres": 69}
    for text, n in texts.items():
        assert [t.value for t in number_tokens(text)] == [n], text


@pytest.mark.parametrize("n", range(0, 101, 7))
def test_digits_in_any_script_are_numbers(n: int) -> None:
    for text in (str(n), str(n).translate(FULL_WIDTH)):
        assert [t.value for t in number_tokens(f"go {text}!")] == [n]


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("3,2,1", [3, 2.1]),  # a decimal comma only at the end; every part is still a number
        ("1,5 minuter", [1.5]),
        ("2.5 min", [2.5]),
        ("hold 1:30", [1]),
        ("3; 2; 1", [3, 2, 1]),
        ("5/4/3/2/1", [5, 4, 3, 2, 1]),
        ("3 \u2192 2 \u2192 1", [3, 2, 1]),
        ("1st", [1]),
        ("4x", [4]),
        ("60%", [60]),
        ("mp3 takten someone often", []),
    ],
)
def test_digits_and_words_in_running_text(text: str, values: list[float]) -> None:
    assert [t.value for t in number_tokens(text)] == values


def test_the_ambiguous_words_are_marked() -> None:
    assert {"one", "en", "ett", "et", "to", "ni", "fire"} == AMBIGUOUS
    tokens = number_tokens("one two en tre")
    assert [t.ambiguous for t in tokens] == [True, False, True, False]
    assert number_tokens("1:30")[0].clock
