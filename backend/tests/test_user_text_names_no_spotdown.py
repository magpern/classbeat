"""No text the backend shows or sends a user names spotdown (owner, 2026-11-27, #1571).

"For the user classbeat is all they know. Spotdown is just a service for classbeat." The user sees
the backend's words in the account mail (``emails.locales``, every language), the pool consent
(``CONSENT_TEXT``) and the job and Spotify failure sentences (``jobs.sentences``,
``spotify.errors``); API errors are reason codes, never prose. Code, logs, docs and ADRs keep the
name. The frontend catalogues have their own guard (``noSpotdownInCatalogues.test.ts``).
"""

from collections.abc import Iterator
from types import ModuleType

import pytest

from classbeat.connections import spotify_consent_policy
from classbeat.emails.locales import da, en, sv
from classbeat.jobs import sentences
from classbeat.spotify import errors

USER_TEXT_MODULES = (spotify_consent_policy, sentences, errors)
MAIL = {"en": en.CATALOGUE, "sv": sv.CATALOGUE, "da": da.CATALOGUE}


def texts(value: object, where: str) -> Iterator[tuple[str, str]]:
    """Every string in ``value``: itself, or inside a mapping, sequence or set, at any depth."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from texts(inner, f"{where}[{key!r}]")
    elif isinstance(value, list | tuple | set | frozenset):
        for i, inner in enumerate(value):
            yield from texts(inner, f"{where}[{i}]")


def module_texts(module: ModuleType) -> Iterator[tuple[str, str]]:
    """The module's own constants (not its docstring, not what it imports by name)."""
    for name, value in vars(module).items():
        if name.startswith("__") or isinstance(value, ModuleType):
            continue
        yield from texts(value, f"{module.__name__}.{name}")


def naming_spotdown(found: Iterator[tuple[str, str]]) -> list[str]:
    return [where for where, text in found if "spotdown" in text.lower()]


@pytest.mark.parametrize("lang", sorted(MAIL))
def test_no_mail_string_names_spotdown(lang: str) -> None:
    catalogue = MAIL[lang]
    assert len(catalogue) > 50
    assert naming_spotdown(texts(catalogue, lang)) == []


@pytest.mark.parametrize("module", USER_TEXT_MODULES, ids=lambda m: m.__name__)
def test_no_consent_or_failure_sentence_names_spotdown(module: ModuleType) -> None:
    assert list(module_texts(module))
    assert naming_spotdown(module_texts(module)) == []


def test_the_consent_text_and_the_song_sentences_are_read() -> None:
    read = dict(module_texts(spotify_consent_policy)) | dict(module_texts(sentences))
    assert read["classbeat.connections.spotify_consent_policy.CONSENT_TEXT"] == (
        spotify_consent_policy.CONSENT_TEXT
    )
    assert any(where.startswith("classbeat.jobs.sentences.SONG_SENTENCES[") for where in read)


def test_the_check_finds_the_name_in_any_case_and_at_any_depth() -> None:
    sample = {"a": "Analysed by Spotdown.", "b": ("x", {"c": "SPOTDOWN"}), "d": "classbeat"}
    assert naming_spotdown(texts(sample, "s")) == ["s['a']", "s['b'][1]['c']"]
