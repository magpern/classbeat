"""What the server adds to the single-page app's ``index.html`` for language-prefixed URLs
(ADR 0027 sections 5 and 6): ``<html lang>``, canonical and hreflang links for the public pages,
``noindex`` for the rest, plus ``/sitemap.xml`` and ``/robots.txt``.

Every string built here comes from ``public_base_url``, the locale manifest, the enabled languages
and ``PUBLIC_PATHS``. Nothing from a request (Host, path, query) is ever put in a response: the
request path only *selects* one of the pre-built pages by comparing it with those lists.
"""

import re
from dataclasses import dataclass
from html import escape

from classbeat import locales
from classbeat.config import Settings

# The pages a search engine may index, as paths after the language ("" is the landing page).
PUBLIC_PATHS: tuple[str, ...] = ("", "/privacy", "/terms")
X_DEFAULT = locales.DEFAULT_LOCALE
NO_CACHE = {"Cache-Control": "no-cache"}
NOINDEX = '<meta name="robots" content="noindex">'

_HTML_LANG = re.compile(r'(<html\b[^>]*?\blang=")[^"]*(")')


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
    public: dict[tuple[str, str], str]  # (language, public path) -> page with canonical + hreflang

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


def build_pages(index: str, settings: Settings) -> SpaPages:
    base = settings.public_base_url.rstrip("/")
    offered = locales.offered_locales(settings)
    by_lang_noindex: dict[str, str] = {}
    public: dict[tuple[str, str], str] = {}
    for code in locales.SUPPORTED_LOCALES:
        # A language that is supported but not enabled is shown in the best offered one by the
        # client: its page says English and is never indexed.
        shown = code if code in offered else X_DEFAULT
        by_lang_noindex[code] = _fill(index, shown, NOINDEX)
        if code not in offered:
            continue
        for page in PUBLIC_PATHS:
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
            public[(code, page)] = _fill(index, code, "\n".join(links))
    return SpaPages(_fill(index, None, NOINDEX), by_lang_noindex, public)


def sitemap(settings: Settings) -> str:
    base = settings.public_base_url.rstrip("/")
    offered = locales.offered_locales(settings)
    rows = ['<?xml version="1.0" encoding="UTF-8"?>']
    rows.append(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">'
    )
    for page in PUBLIC_PATHS:
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
