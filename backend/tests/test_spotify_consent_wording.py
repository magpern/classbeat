"""The consent sentence says the working playlist is public, and the pool failure sentence never
tells the user to make it private.

Roadmap Amendment 30 (issue #1287): every playlist classbeat creates is public from the start, so
MOWL's Spotify import can list it. The version-1 wording ("We ask Spotify for a private playlist")
became untrue, so the wording is version 2; a version-1 consent stands (it already said the
playlist may be public and that anyone with its link can open it; ADR 0009 section 9).
"""

from classbeat.jobs.sentences import SONG_SENTENCES
from classbeat.spotify.routes import CONSENT_TEXT, CONSENT_VERSION


def test_consent_text_says_the_playlist_is_public() -> None:
    assert "classbeat working copy" in CONSENT_TEXT
    assert "It is public" in CONSENT_TEXT
    assert "Spotify profile" in CONSENT_TEXT
    assert "anyone with its link" in CONSENT_TEXT
    assert "private" not in CONSENT_TEXT.lower()


def test_consent_version_is_two_for_the_public_wording() -> None:
    assert CONSENT_VERSION == 2


def test_pool_unusable_does_not_tell_the_user_to_make_it_private() -> None:
    sentence = SONG_SENTENCES["pool_unusable"]
    assert "private" not in sentence.lower()
    assert "set it up again" in sentence.lower()
