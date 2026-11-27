"""The consent sentence says what happens to the working playlist and its songs, and the pool
failure sentence never tells the user to make it private.

Roadmap Amendment 30 (issue #1287): every playlist classbeat creates is public from the start, so
MOWL's Spotify import can list it (version 2). #1540: MOWL does not analyse the songs. classbeat
links the playlist in MOWL so the class can be timed to it, and the songs are analysed (by spotdown,
ADR 0040) from their title, artists, length and Spotify explicit flag, and a digest of the track id.
That purpose and those fields were never in versions 1 and 2, so the wording is version 3 and only a
version-3 consent stands (ADR 0009 section 9). The words name classbeat as the analyser, never
spotdown (owner, 2026-11-27, #1571: classbeat is all the user knows); no fact changed: still 3.
"""

import re
from pathlib import Path

import pytest

from classbeat.connections.spotify_consent_policy import consent_current
from classbeat.jobs.sentences import SONG_SENTENCES
from classbeat.spotify.routes import CONSENT_TEXT, CONSENT_VERSION

# The composer's consent card (``MusicConsentCard``) shows this sentence above ``CONSENT_TEXT``.
FRONTEND = Path(__file__).resolve().parents[2] / "frontend/src"
WORKSPACE_EN = FRONTEND / "i18n/workspace.en.ts"
# Frontend tests whose ``CONSENT_TEXT`` fixture says it is the text the server sends.
FIXTURES = ("pages/accountTabs.test.tsx", "components/account/Spotify.test.tsx")
# What both copies must say: the MOWL link's purpose, who analyses, what it gets, and from where.
SHARED_FACTS = (
    "links it in MOWL",
    "can be timed to it",
    "classbeat analyses the songs.",
    "title, artists, length",
    "the explicit-lyrics flag from Spotify",
    "a code derived from the song\u2019s Spotify track id",
    "source other than Spotify",
    "public",
)


def catalogue_message(source: str, key: str) -> str:
    """The single-quoted string ``key`` holds in a catalogue file, escapes read by ``unescape``."""
    found = re.search(rf"'{re.escape(key)}': '((?:[^'\\]|\\.)*)'", source)
    assert found is not None
    return unescape(found.group(1))


ESCAPES = {"\\": "\\", "'": "'", "n": "\n"}


def unescape(raw: str) -> str:
    """A TypeScript single-quoted string's text: backslash-backslash, backslash-quote and
    backslash-n are read in one pass; any other escape fails here instead of reaching the
    comparison as a stray backslash."""

    def one(found: re.Match[str]) -> str:
        assert found.group(1) in ESCAPES, f"unhandled escape \\{found.group(1)} in {raw!r}"
        return ESCAPES[found.group(1)]

    return re.sub(r"\\(.)", one, raw, flags=re.DOTALL)


def card_body() -> str:
    return catalogue_message(WORKSPACE_EN.read_text("utf-8"), "cw.music.consent.body")


def fixture_text(path: str) -> str:
    source = (FRONTEND / path).read_text("utf-8")
    found = re.search(r"const CONSENT_TEXT =\s*'((?:[^'\\]|\\.)*)'", source)
    assert found is not None
    return unescape(found.group(1))


def mowl_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.:])\s+", text) if "MOWL" in s]


def test_the_card_regex_reads_past_an_escaped_quote() -> None:
    source = "'a': 'x', 'k': 'It\\'s linked in MOWL. Then it is analysed.', 'z': 'y'"
    assert catalogue_message(source, "k") == "It's linked in MOWL. Then it is analysed."
    assert mowl_sentences(catalogue_message(source, "k")) == ["It's linked in MOWL."]


def test_the_helpers_read_a_backslash_and_a_newline_escape_in_one_pass() -> None:
    # The old helper only turned backslash-quote into a quote: it left the other two escapes as
    # two characters, and turned the second half of an escaped backslash plus a quote wrongly.
    assert unescape("a\\\\b") == "a\\b"
    assert unescape("a\\nb") == "a\nb"
    assert unescape("\\\\n") == "\\n"  # an escaped backslash then ``n``, not a newline
    assert unescape("\\\\\\'") == "\\'"
    source = "'k': 'one\\\\two\\nthree\\'s', 'z': 'y'"
    assert catalogue_message(source, "k") == "one\\two\nthree's"


def test_an_escape_the_helpers_do_not_read_fails_instead_of_passing_through() -> None:
    for raw in ("\\u2019", "\\t", '\\"', "\\x41"):
        with pytest.raises(AssertionError, match="unhandled escape"):
            unescape(raw)
    with pytest.raises(AssertionError, match="unhandled escape"):
        catalogue_message("'k': 'a\\tb'", "k")


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


def test_consent_text_names_the_mowl_link_and_what_the_analysis_uses() -> None:
    assert "links it in MOWL so your class can be timed to it" in CONSENT_TEXT
    assert "classbeat analyses the songs. For each candidate song it uses the following:" in (
        CONSENT_TEXT
    )
    assert "title, artists, length, the explicit-lyrics flag from Spotify" in CONSENT_TEXT
    assert "a code derived from the song\u2019s Spotify track id" in CONSENT_TEXT
    assert "source other than Spotify" in CONSENT_TEXT


@pytest.mark.parametrize("path", FIXTURES)
def test_the_frontend_consent_fixtures_are_the_text_the_server_sends(path: str) -> None:
    assert fixture_text(path) == CONSENT_TEXT


def test_the_composer_test_expects_the_card_as_it_is() -> None:
    expected = (FRONTEND / "pages/composerMusicQuestion.test.tsx").read_text("utf-8")
    assert f"'{card_body()}'" in expected


def test_the_song_owns_its_track_id_in_both_copies() -> None:
    # "it" is classbeat; "its" would be read as classbeat's too (#1564).
    for text in (CONSENT_TEXT, card_body()):
        assert "derived from the song\u2019s Spotify track id" in text
        assert "its Spotify track id" not in text


@pytest.mark.parametrize("text", [CONSENT_TEXT, card_body()], ids=["consent", "card"])
def test_no_consent_copy_names_spotdown(text: str) -> None:
    # #1571: spotdown is classbeat's own service; the user is told classbeat analyses the songs.
    assert "spotdown" not in text.lower()


def test_consent_version_stays_three_for_the_analysis_wording() -> None:
    # #1571 reworded version 3 with no fact added or removed, before any release carried it.
    assert CONSENT_VERSION == 3


@pytest.mark.parametrize(("version", "stands"), [(None, False), (1, False), (2, False), (3, True)])
def test_only_a_consent_to_the_current_wording_stands(version: int | None, stands: bool) -> None:
    assert consent_current(version) is stands


def test_pool_unusable_does_not_tell_the_user_to_make_it_private() -> None:
    sentence = SONG_SENTENCES["pool_unusable"]
    assert "private" not in sentence.lower()
    assert "set it up again" in sentence.lower()
