"""The link-preview head tags of the public pages (issues 854 and 868): ``og:image`` and its size,
``twitter:card``, and a title, description and locale in the page's own language.

Pure: text and string building only, no I/O. The copy follows ADR 0028 section 2 (title short, a
description of at most 25 words, "classbeat" only as the brand in a title, second person for the
reader). Every value is escaped here; the language is chosen by the caller exactly as for the
canonical and hreflang links (``spa.build_pages``), and a language without copy falls back to
English, never to a string built from a request.
"""

from html import escape

DEFAULT_LANGUAGE = "en"
# The link-preview picture (issue 854): one file, 1200 x 630, in the frontend's ``public/img``.
OG_IMAGE_PATH = "/img/og-share.jpg"
OG_IMAGE_SIZE = (1200, 630)
DESCRIPTION_MAX_WORDS = 25

# Open Graph wants language_TERRITORY. Keyed by the manifest's two-letter code.
OG_LOCALE = {"en": "en_US", "sv": "sv_SE", "da": "da_DK"}

# (title, description) per language and public page ("" is the landing page).
COPY: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "": (
            "classbeat | Your class, timed to the music",
            "Describe the ride and we pick the songs. Design indoor-cycling classes "
            "timed to the music and save them to MOWL.",
        ),
        "/privacy": (
            "Privacy | classbeat",
            "What we keep about you, why we keep it, and how you export or delete it.",
        ),
        "/terms": (
            "Terms | classbeat",
            "The terms for classbeat: what you can do, what we do and what we do not promise.",
        ),
    },
    "sv": {
        "": (
            "classbeat | Ditt pass, i takt med musiken",
            "Beskriv turen så väljer vi låtarna. Utforma spinningpass i takt med musiken "
            "och spara dem i MOWL.",
        ),
        "/privacy": (
            "Integritet | classbeat",
            "Vad vi sparar om dig, varför vi sparar det och hur du exporterar eller raderar det.",
        ),
        "/terms": (
            "Villkor | classbeat",
            "Villkoren för classbeat: vad du får göra, vad vi gör och vad vi inte lovar.",
        ),
    },
    "da": {
        "": (
            "classbeat | Dit hold, i takt med musikken",
            "Beskriv turen, så vælger vi sangene. Design spinninghold i takt med musikken "
            "og gem dem i MOWL.",
        ),
        "/privacy": (
            "Privatliv | classbeat",
            "Hvad vi gemmer om dig, hvorfor, og hvordan du eksporterer eller sletter det.",
        ),
        "/terms": (
            "Vilkår | classbeat",
            "Vilkårene for classbeat: hvad du må, hvad vi gør, og hvad vi ikke lover.",
        ),
    },
}


def _esc(value: str) -> str:
    return escape(value, quote=True)


def _meta(prop: str, content: str) -> str:
    return f'<meta property="{prop}" content="{_esc(content)}">'


def og_tags(base: str, lang: str, page: str) -> list[str]:
    """The preview tags of one public page. ``base`` has no trailing slash."""
    width, height = OG_IMAGE_SIZE
    code = lang if lang in COPY and lang in OG_LOCALE else DEFAULT_LANGUAGE
    title, description = COPY[code][page]
    return [
        _meta("og:title", title),
        _meta("og:description", description),
        _meta("og:locale", OG_LOCALE[code]),
        _meta("og:image", base + OG_IMAGE_PATH),
        f'<meta property="og:image:width" content="{width}">',
        f'<meta property="og:image:height" content="{height}">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
