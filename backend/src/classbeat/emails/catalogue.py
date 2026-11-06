"""Strings by locale. ``mail_locale`` is the one place the language of a mail is decided from what
is stored (ML, #671); the callers that know better (a request) pass the code to ``build``.

A locale is an allowlisted code: ``LOCALES`` is the set of catalogues the build carries. Whether a
language is *offered* (``classbeat.locales``: the manifest, ``CLASSBEAT_LOCALES``) is a separate
question, so the Swedish and Danish catalogues exist from ML6 but no stored code reaches them until
ML7 adds them to the manifest. A string a catalogue lacks is English (``untranslated.py`` lists
which may be missing).
"""

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from classbeat import locales
from classbeat.emails.locales import da, en, sv
from classbeat.emails.locales.en import S

if TYPE_CHECKING:
    from classbeat.config import Settings

DEFAULT = "en"
LOCALES: dict[str, dict[S, str]] = {"en": en.CATALOGUE, "sv": sv.CATALOGUE, "da": da.CATALOGUE}
_FORMAT_WHEN: dict[str, Callable[[datetime], str]] = {
    "en": en.format_when,
    "sv": sv.format_when,
    "da": da.format_when,
}


def known(locale: object) -> str:
    """``locale`` itself when a catalogue exists for it, else English (never echoes input)."""
    return locale if isinstance(locale, str) and locale in LOCALES else DEFAULT


def mail_locale(user: object | None = None, settings: "Settings | None" = None) -> str:
    """The language of a user's mail: their stored ``locale`` when it is offered (and has a
    catalogue), else English. ``user`` is an object with a ``locale`` attribute (a session, a row)
    or the code itself. There is no browser at send time, so ``NULL`` ("follow this browser")
    sends English (ADR 0023 section 2). ``settings`` also applies ``CLASSBEAT_LOCALES``."""
    code: Any = user if isinstance(user, str) else getattr(user, "locale", None)
    offered = locales.SUPPORTED_LOCALES if settings is None else locales.offered_locales(settings)
    if isinstance(code, str) and code in LOCALES and code in offered:
        return code
    return DEFAULT


def format_when(locale: str, when: datetime) -> str:
    return _FORMAT_WHEN[known(locale)](when)


def translator(locale: str) -> Callable[..., str]:
    table = LOCALES[known(locale)]

    def t(string_id: S, **params: Any) -> str:
        return (table.get(string_id) or en.CATALOGUE[string_id]).format(**params)

    return t
