"""Per-language ``og:title``, ``og:description`` and ``og:locale`` on the published pages (#868).

The text is the page's own title and description from the prerender (ADR 0030 section 3), so the
preview and the ``<title>`` cannot differ. The language is the one the page is served in (the same
``code`` as its canonical and hreflang links, which stay as ADR 0027 has them). The copy rules
(ADR 0028 section 2) are tested on the catalogue: ``frontend/src/publicSite/metaCopy.test.ts``.
No database is reached.
"""

import html
import re
from dataclasses import replace

import pytest

from classbeat import locales, spa, spa_preview
from classbeat.config import Settings
from spatools import INDEX, fake_content, fake_text
from test_web_language import BASE, OFFERED, PUBLIC, alternates, canonical

LOCALE = {"en": "en_US", "sv": "sv_SE", "da": "da_DK"}


def preview(page_html: str) -> dict[str, str]:
    found = re.findall(
        r'<meta property="og:(title|description|locale)" content="([^"]*)">', page_html
    )
    assert len(found) == len({k for k, _ in found}), "each tag appears once"
    return {key: html.unescape(value) for key, value in found}


def test_every_supported_language_has_an_open_graph_locale() -> None:
    assert set(locales.SUPPORTED_LOCALES) <= set(spa_preview.OG_LOCALE), "add the new language"


@pytest.mark.parametrize("code", OFFERED)
@pytest.mark.parametrize("page", PUBLIC)
def test_a_published_page_previews_its_own_title_and_description(code: str, page: str) -> None:
    page_html = spa.build_pages(INDEX, Settings(), fake_content()).public[(code, page)]
    tags = preview(page_html)
    text = fake_text(code, page)
    assert tags["locale"] == spa_preview.OG_LOCALE[code]
    assert (tags["title"], tags["description"]) == (text.title, text.description)
    # the same words as the <title> and the description of the page itself
    assert page_html.count(f"<title>{html.escape(text.title)}</title>") == 1
    assert f'<meta name="description" content="{html.escape(text.description)}" />' in page_html


def test_pages_that_are_not_published_carry_no_title_description_or_locale_tags() -> None:
    # PP8 published every path, so the shipped list has no draft: draft /terms for this test
    drafted = "/terms"
    content = replace(
        fake_content(), published=tuple(p for p in PUBLIC if p != drafted), draft=(drafted,)
    )
    pages = spa.build_pages(INDEX, Settings(), content)
    for page_html in (pages.plain_noindex, *pages.by_lang_noindex.values()):
        assert preview(page_html) == {}
    for code in OFFERED:
        for page in (drafted,):
            assert preview(pages.public[(code, page)]) == {}


def test_canonical_and_hreflang_are_unchanged_by_the_preview_tags() -> None:
    pages = spa.build_pages(INDEX, Settings(), fake_content())
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


def test_a_language_without_an_open_graph_locale_falls_back_to_englishs() -> None:
    tags = spa_preview.og_tags(BASE, "zz", "Title", "Description")
    assert '<meta property="og:locale" content="en_US">' in tags
    assert '<meta property="og:title" content="Title">' in tags


def test_values_are_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    hostile = "\"><script>alert(1)</script> & 'x'"
    monkeypatch.setitem(spa_preview.OG_LOCALE, "sv", hostile)
    content = fake_content()
    texts = dict(content.text)
    texts[("sv", "")] = replace(texts[("sv", "")], title=hostile, description=hostile)
    content = replace(content, text=texts)
    page_html = spa.build_pages(INDEX, Settings(), content).public[("sv", "")]
    assert "<script>alert" not in page_html
    # the <title>, the description, og:title, og:description and og:locale: five escaped places
    escaped = "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#x27;x&#x27;"
    assert page_html.count(escaped) == 5
    assert preview(page_html) == {"title": hostile, "description": hostile, "locale": hostile}
