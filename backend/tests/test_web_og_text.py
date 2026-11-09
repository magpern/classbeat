"""Per-language ``og:title``, ``og:description`` and ``og:locale`` on the public pages (issue 868).

The language is the one the page is served in (the same ``code`` as its canonical and hreflang
links, which stay as ADR 0027 has them). Copy rules: ADR 0028 section 2. No database is reached.
"""

import html
import re

import pytest

from classbeat import locales, spa, spa_preview
from classbeat.config import Settings
from test_web_language import BASE, INDEX, OFFERED, PUBLIC, alternates, canonical

LOCALE = {"en": "en_US", "sv": "sv_SE", "da": "da_DK"}
# Words that only the other languages use: a Swedish page must not carry Danish text and so on.
SWEDISH_ONLY = ("Beskriv turen så", "Ditt pass", "Villkor", "Integritet")
DANISH_ONLY = ("Beskriv turen, så", "Dit hold", "Vilkår", "Privatliv")


def preview(page_html: str) -> dict[str, str]:
    found = re.findall(
        r'<meta property="og:(title|description|locale)" content="([^"]*)">', page_html
    )
    assert len(found) == len({k for k, _ in found}), "each tag appears once"
    return {key: html.unescape(value) for key, value in found}


def test_the_three_languages_have_copy_and_a_locale() -> None:
    assert set(locales.SUPPORTED_LOCALES) <= set(spa_preview.COPY), "add copy for the new language"
    assert set(locales.SUPPORTED_LOCALES) <= set(spa_preview.OG_LOCALE)
    for code in locales.SUPPORTED_LOCALES:
        assert set(spa_preview.COPY[code]) == set(PUBLIC)


@pytest.mark.parametrize("code", ["en", "sv", "da"])
@pytest.mark.parametrize("page", PUBLIC)
def test_a_public_page_has_title_description_and_locale_in_its_language(
    code: str, page: str
) -> None:
    tags = preview(spa.build_pages(INDEX, Settings()).public[(code, page)])
    assert tags["locale"] == LOCALE[code]
    assert tags["title"] == spa_preview.COPY[code][page][0]
    assert tags["description"] == spa_preview.COPY[code][page][1]


def test_the_texts_differ_per_language_and_do_not_mix() -> None:
    pages = spa.build_pages(INDEX, Settings())
    for page in PUBLIC:
        titles = {preview(pages.public[(code, page)])["title"] for code in ("en", "sv", "da")}
        assert len(titles) == 3
    sv = " ".join(preview(pages.public[("sv", p)])["title"] for p in PUBLIC)
    da = " ".join(preview(pages.public[("da", p)])["title"] for p in PUBLIC)
    assert not any(word in da for word in SWEDISH_ONLY)
    assert not any(word in sv for word in DANISH_ONLY)


def test_the_landing_titles_in_each_language_match_the_apps_own_headline() -> None:
    pages = spa.build_pages(INDEX, Settings())
    expected = {
        "en": "Your class, timed to the music",
        "sv": "Ditt pass, i takt med musiken",
        "da": "Dit hold, i takt med musikken",
    }
    for code, headline in expected.items():
        assert headline in preview(pages.public[(code, "")])["title"]


# One distinctive glossary word per language that each landing description must use.
LANDING_WORD = {"en": "indoor-cycling", "sv": "inomhuscykling", "da": "indendørscykling"}


def test_each_landing_description_uses_its_languages_own_wording_and_no_trademark() -> None:
    for code, word in LANDING_WORD.items():
        assert word in spa_preview.COPY[code][""][1]
    for code, pages in spa_preview.COPY.items():
        for title, description in pages.values():
            assert "spinning" not in f"{title} {description}".lower(), code


def test_the_copy_follows_the_length_rules() -> None:
    for code, pages in spa_preview.COPY.items():
        for page, (title, description) in pages.items():
            assert 0 < len(title) <= 60, (code, page)
            assert "classbeat" in title, (code, page)  # the brand belongs in a title (ADR 0028 s2)
            assert 0 < len(description.split()) <= spa_preview.DESCRIPTION_MAX_WORDS, (code, page)
            assert "classbeat" not in description.split()[:1], "never the sentence's subject"


def test_pages_that_are_not_public_carry_no_title_description_or_locale() -> None:
    pages = spa.build_pages(INDEX, Settings())
    for page_html in (pages.plain_noindex, *pages.by_lang_noindex.values()):
        assert preview(page_html) == {}


def test_canonical_and_hreflang_are_unchanged_by_the_preview_tags() -> None:
    pages = spa.build_pages(INDEX, Settings())
    for code in OFFERED:
        for page in PUBLIC:
            page_html = pages.public[(code, page)]
            assert canonical(page_html) == [f"{BASE}/{code}{page}"]
            assert alternates(page_html) == {
                **{other: f"{BASE}/{other}{page}" for other in OFFERED},
                "x-default": f"{BASE}/en{page}",
            }
            # the head still lists canonical first, then the alternates, then the preview tags
            head = page_html[page_html.index("<head>") : page_html.index("</head>")]
            assert head.index('rel="canonical"') < head.index("hreflang") < head.index("og:title")


def test_a_language_without_copy_falls_back_to_english_text_and_locale() -> None:
    tags = spa_preview.og_tags(BASE, "zz", "/privacy")
    assert (
        tags[0] == f'<meta property="og:title" content="{spa_preview.COPY["en"]["/privacy"][0]}">'
    )
    assert '<meta property="og:locale" content="en_US">' in tags


def test_values_are_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    hostile = "\"><script>alert(1)</script> & 'x'"
    monkeypatch.setitem(spa_preview.COPY, "sv", {p: (hostile, hostile) for p in PUBLIC})
    monkeypatch.setitem(spa_preview.OG_LOCALE, "sv", hostile)
    page_html = spa.build_pages(INDEX, Settings()).public[("sv", "")]
    assert "<script>alert" not in page_html
    assert (
        page_html.count("&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#x27;x&#x27;") == 3
    )
    # the escaped value reads back as the original, and the tag is still one well-formed element
    assert preview(page_html) == {"title": hostile, "description": hostile, "locale": hostile}
    assert page_html.count("<meta property=") == 6
