"""The link-preview head tags of the public pages (issues 854 and 868): ``og:image`` and its size,
``twitter:card``, and a title, description and locale in the page's own language.

Pure: string building only, no I/O. The title and description are the page's own text from the
prerender (ADR 0030 section 3; their copy rules, ADR 0028 section 2, are tested on the catalogue).
Every value is escaped here; the language is chosen by the caller exactly as for the canonical and
hreflang links (``spa.build_pages``), and a language without an Open Graph locale falls back to
English's, never to a string built from a request.
"""

from html import escape

DEFAULT_LANGUAGE = "en"
# The link-preview picture (issue 854): one file, 1200 x 630, in the frontend's ``public/img``.
OG_IMAGE_PATH = "/img/og-share.jpg"
OG_IMAGE_SIZE = (1200, 630)
DESCRIPTION_MAX_WORDS = 25

# Open Graph wants language_TERRITORY. Keyed by the manifest's two-letter code.
OG_LOCALE = {"en": "en_US", "sv": "sv_SE", "da": "da_DK"}


def _esc(value: str) -> str:
    return escape(value, quote=True)


def _meta(prop: str, content: str) -> str:
    return f'<meta property="{prop}" content="{_esc(content)}">'


def og_tags(base: str, lang: str, title: str, description: str) -> list[str]:
    """The preview tags of one published page. ``base`` has no trailing slash. ``title`` and
    ``description`` are the page's own, from the prerender (the ``meta.*`` catalogue keys)."""
    width, height = OG_IMAGE_SIZE
    locale = OG_LOCALE.get(lang, OG_LOCALE[DEFAULT_LANGUAGE])
    return [
        _meta("og:title", title),
        _meta("og:description", description),
        _meta("og:locale", locale),
        _meta("og:image", base + OG_IMAGE_PATH),
        f'<meta property="og:image:width" content="{width}">',
        f'<meta property="og:image:height" content="{height}">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
