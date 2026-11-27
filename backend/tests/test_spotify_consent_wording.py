"""The consent sentence says what happens to the working playlist and its songs, and the pool
failure sentence never tells the user to make it private.

Roadmap Amendment 30 (issue #1287): every playlist classbeat creates is public from the start, so
MOWL's Spotify import can list it (version 2). #1540: MOWL does not analyse the songs. classbeat
links the playlist in MOWL so the class can be timed to it, and spotdown analyses the songs from
their title, artists and length (ADR 0040). spotdown is a recipient versions 1 and 2 never named, so
the wording is version 3 and only a version-3 consent stands (ADR 0009 section 9).
"""

import re
from pathlib import Path

import pytest

from classbeat.connections.spotify_consent_policy import consent_current
from classbeat.jobs.sentences import SONG_SENTENCES
from classbeat.spotify.routes import CONSENT_TEXT, CONSENT_VERSION

# The composer's consent card (``MusicConsentCard``) shows this sentence above ``CONSENT_TEXT``.
WORKSPACE_EN = Path(__file__).resolve().parents[2] / "frontend/src/i18n/workspace.en.ts"
# What both copies must say: the MOWL link's purpose, who analyses, what it gets, and from where.
SHARED_FACTS = (
    "links it in MOWL",
    "can be timed to it",
    "spotdown",
    "title, artists and length",
    "source other than Spotify",
    "public",
)


def card_body() -> str:
    found = re.search(r"'cw\.music\.consent\.body': '([^']*)'", WORKSPACE_EN.read_text("utf-8"))
    assert found is not None
    return found.group(1)


def mowl_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.:])\s+", text) if "MOWL" in s]


def test_consent_text_says_the_playlist_is_public() -> None:
    assert "classbeat working copy" in CONSENT_TEXT
    assert "It is public" in CONSENT_TEXT
    assert "Spotify profile" in CONSENT_TEXT
    assert "anyone with its link" in CONSENT_TEXT
    assert "private" not in CONSENT_TEXT.lower()


@pytest.mark.parametrize("text", [CONSENT_TEXT, card_body()], ids=["consent", "card"])
def test_no_consent_copy_says_mowl_analyses_or_listens_to_the_songs(text: str) -> None:
    assert mowl_sentences(text)
    for sentence in mowl_sentences(text):
        assert not re.search(r"analy[sz]|listen", sentence, re.IGNORECASE), sentence


@pytest.mark.parametrize("fact", SHARED_FACTS)
def test_the_backend_and_the_card_say_the_same_thing(fact: str) -> None:
    assert fact in CONSENT_TEXT
    assert fact in card_body()


def test_consent_text_names_the_mowl_link_and_what_spotdown_gets() -> None:
    assert "links it in MOWL so your class can be timed to it" in CONSENT_TEXT
    assert "analysed by spotdown" in CONSENT_TEXT
    assert "title, artists and length" in CONSENT_TEXT
    assert "source other than Spotify" in CONSENT_TEXT


def test_consent_version_is_three_for_the_spotdown_wording() -> None:
    assert CONSENT_VERSION == 3


@pytest.mark.parametrize(("version", "stands"), [(None, False), (1, False), (2, False), (3, True)])
def test_only_a_consent_to_the_current_wording_stands(version: int | None, stands: bool) -> None:
    assert consent_current(version) is stands


def test_pool_unusable_does_not_tell_the_user_to_make_it_private() -> None:
    sentence = SONG_SENTENCES["pool_unusable"]
    assert "private" not in sentence.lower()
    assert "set it up again" in sentence.lower()
