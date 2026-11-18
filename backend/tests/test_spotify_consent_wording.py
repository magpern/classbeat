"""The consent sentence and the pool failure sentence do not promise a private playlist.

Roadmap Amendment 27 (issue #1162): a playlist classbeat creates is asked for private, but Spotify
may make it public, and classbeat keeps using it. The clarification keeps ``CONSENT_VERSION`` at 1
(ADR 0008 section 2), so existing consents stand.
"""

from classbeat.jobs.sentences import SONG_SENTENCES
from classbeat.spotify.routes import CONSENT_TEXT, CONSENT_VERSION


def test_consent_text_says_spotify_may_make_the_playlist_public() -> None:
    assert "classbeat working copy" in CONSENT_TEXT
    assert "may make it public" in CONSENT_TEXT
    assert "anyone with its link" in CONSENT_TEXT
    assert "keeps using it" in CONSENT_TEXT
    assert "one private working playlist" not in CONSENT_TEXT


def test_consent_version_is_unchanged_by_the_clarification() -> None:
    assert CONSENT_VERSION == 1


def test_pool_unusable_does_not_tell_the_user_to_make_it_private() -> None:
    sentence = SONG_SENTENCES["pool_unusable"]
    assert "private" not in sentence.lower()
    assert "set it up again" in sentence.lower()
