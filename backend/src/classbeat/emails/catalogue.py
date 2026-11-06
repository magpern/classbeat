"""Strings by locale. ``mail_locale`` is the one place multilingual mail changes (ML, #657)."""

from collections.abc import Callable
from typing import Any

from classbeat.emails.locales.en import CATALOGUE as EN
from classbeat.emails.locales.en import S

LOCALES = {"en": EN}


def mail_locale(user: object | None = None) -> str:
    """The language of a user's mail. Always English until milestone ML."""
    return "en"


def translator(locale: str) -> Callable[..., str]:
    table = LOCALES[locale]

    def t(string_id: S, **params: Any) -> str:
        return table[string_id].format(**params)

    return t
