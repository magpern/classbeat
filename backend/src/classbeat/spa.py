"""What the server adds to the single-page app's ``index.html`` for language-prefixed URLs
(ADR 0027 sections 5 and 6, ADR 0030 section 4): ``<html lang>``, and for the public pages the
prerendered ``<title>``, description and ``#root`` body (``spa_content``), canonical and hreflang
links and the link-preview tags (``spa_preview``) of the published ones, ``noindex`` for the draft
and the app pages, plus ``/sitemap.xml`` and ``/robots.txt``.

Every string built here comes from ``public_base_url``, the locale manifest, the enabled languages
and the build's ``PublicContent``. Nothing from a request (Host, path, query) is ever put in a
response: the request path only *selects* one of the pre-built pages by comparing it with those
lists.
"""

import re
from dataclasses import dataclass
from html import escape

from classbeat import locales
from classbeat.config import Settings
from classbeat.spa_content import PageText, PublicContent
from classbeat.spa_preview import og_tags

X_DEFAULT = locales.DEFAULT_LOCALE
NO_CACHE = {"Cache-Control": "no-cache"}
NOINDEX = '<meta name="robots" content="noindex">'

_HTML_LANG = re.compile(r'(<html\b[^>]*?\blang=")[^"]*(")')
# The three things a prerendered page replaces in ``index.html``, each present exactly once.
_TITLE = re.compile(r"<title>[^<]*</title>")
_DESCRIPTION = re.compile(r'<meta\s+name="description"\s+content="[^"]*"\s*/?>')
_ROOT = '<div id="root"></div>'


def is_unknown_language(first_segment: str) -> bool:
    """Two lowercase letters that are not in the manifest (``/fr/privacy``): a 404 (item 5)."""
    return bool(locales.CODE.fullmatch(first_segment)) and (
        first_segment not in locales.SUPPORTED_LOCALES
    )


def _esc(value: str) -> str:
    return escape(value, quote=True)


def _page_url(base: str, lang: str, page: str) -> str:
    return f"{base}/{lang}{page}"


@dataclass(frozen=True, slots=True)
class SpaPages:
    """Pre-built ``index.html`` variants. ``select`` maps a request path to one of them."""

    plain_noindex: str  # unprefixed, unknown or non-public pages: noindex, language untouched
    by_lang_noindex: dict[str, str]
    # (language, listed path) -> prerendered page: published has canonical + hreflang, draft noindex
    public: dict[tuple[str, str], str]

    def select(self, path: str) -> str:
        """``path`` is the request path without its leading slash. Only a comparison."""
        first, _, rest = path.partition("/")
        if first not in self.by_lang_noindex:
            return self.plain_noindex
        # one trailing slash is the same page (the client replaces it away); nothing else is
        page = "/" + rest if rest else ""
        page = page.removesuffix("/")
        return self.public.get((first, page)) or self.by_lang_noindex[first]


def _fill(index: str, lang: str | None, head: str) -> str:
    out = index
    if lang is not None:
        out = _HTML_LANG.sub(lambda m: f"{m.group(1)}{_esc(lang)}{m.group(2)}", out, count=1)
    marker = out.find("</head>")
    return out if marker < 0 else f"{out[:marker]}{head}\n{out[marker:]}"


def _prerendered(index: str, text: PageText) -> str:
    """``index`` with the page's title, description and body (lambdas: no backslash escapes)."""
    out = _TITLE.sub(lambda _: f"<title>{_esc(text.title)}</title>", index, count=1)
    meta = f'<meta name="description" content="{_esc(text.description)}" />'
    out = _DESCRIPTION.sub(lambda _: meta, out, count=1)
    return out.replace(_ROOT, f'<div id="root">{text.body}</div>', 1)


def _require_once(index: str) -> None:
    found = (len(_TITLE.findall(index)), len(_DESCRIPTION.findall(index)), index.count(_ROOT))
    if found != (1, 1, 1) or index.count("<title") != 1:
        raise RuntimeError("index.html needs exactly one title, description and empty #root")


def _published_head(base: str, code: str, page: str, offered: tuple[str, ...]) -> list[str]:
    links = [f'<link rel="canonical" href="{_esc(_page_url(base, code, page))}">']
    links += [
        f'<link rel="alternate" hreflang="{_esc(other)}" '
        f'href="{_esc(_page_url(base, other, page))}">'
        for other in offered
    ]
    links.append(
        f'<link rel="alternate" hreflang="x-default" '
        f'href="{_esc(_page_url(base, X_DEFAULT, page))}">'
    )
    return links


def build_pages(index: str, settings: Settings, content: PublicContent) -> SpaPages:
    base = settings.public_base_url.rstrip("/")
    offered = locales.offered_locales(settings)
    by_lang_noindex: dict[str, str] = {}
    public: dict[tuple[str, str], str] = {}
    _require_once(index)
    for code in locales.SUPPORTED_LOCALES:
        # A language that is supported but not enabled is shown in the best offered one by the
        # client: its page says English and is never indexed.
        shown = code if code in offered else X_DEFAULT
        by_lang_noindex[code] = _fill(index, shown, NOINDEX)
        if code not in offered:
            continue
        for page in content.published:
            text = content.text[(code, page)]
            head = _published_head(base, code, page, offered)
            head += og_tags(base, code, text.title, text.description)
            public[(code, page)] = _fill(_prerendered(index, text), code, "\n".join(head))
        for page in content.draft:
            # Same body and takeover, but never indexed and no new link in the head (ADR 0030 s2).
            public[(code, page)] = _fill(
                _prerendered(index, content.text[(code, page)]), code, NOINDEX
            )
    return SpaPages(_fill(index, None, NOINDEX), by_lang_noindex, public)


def sitemap(settings: Settings, published: tuple[str, ...]) -> str:
    base = settings.public_base_url.rstrip("/")
    offered = locales.offered_locales(settings)
    rows = ['<?xml version="1.0" encoding="UTF-8"?>']
    rows.append(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">'
    )
    for page in published:
        for code in offered:
            rows.append(f"<url><loc>{_esc(_page_url(base, code, page))}</loc>")
            for other in offered:
                rows.append(
                    f'<xhtml:link rel="alternate" hreflang="{_esc(other)}" '
                    f'href="{_esc(_page_url(base, other, page))}"/>'
                )
            rows.append(
                f'<xhtml:link rel="alternate" hreflang="x-default" '
                f'href="{_esc(_page_url(base, X_DEFAULT, page))}"/></url>'
            )
    rows.append("</urlset>")
    return "\n".join(rows) + "\n"


def robots(settings: Settings) -> str:
    # Nothing is blocked: a crawler must fetch a page and its scripts to see its noindex (item 6).
    base = settings.public_base_url.rstrip("/")
    return f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n"
