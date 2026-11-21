"""The listening-preview scopes as pure rules (ADR 0042 section 7.1, #1393): asked for only on
opt-in, kept as classbeat's own scope names, and ``preview_granted`` true only with both. No I/O."""

from urllib.parse import parse_qs, urlsplit

import pytest

from classbeat.spotify.flow import (
    PREVIEW_SCOPE,
    PREVIEW_SCOPES,
    SCOPE,
    authorize_url,
    new_state,
    new_verifier,
    preview_granted,
    scope_for,
    scope_granted,
    stored_scopes,
)

BASE = "playlist-modify-private playlist-modify-public playlist-read-private"
PREVIEW = f"{BASE} user-modify-playback-state user-read-playback-state"


def scope_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["scope"][0]


def url(**scope: str) -> str:
    return authorize_url(
        client_id="a" * 32,
        redirect_uri="https://classbeat.test/oauth/spotify/callback",
        state=new_state(),
        verifier=new_verifier(),
        **scope,
    )


def test_no_preview_scope_is_asked_for_without_the_opt_in() -> None:
    assert scope_for(preview=False) == SCOPE
    assert scope_of(url()) == SCOPE
    assert scope_of(url(scope=scope_for(preview=False))) == SCOPE
    for scope in PREVIEW_SCOPES:
        assert scope not in SCOPE.split()


def test_the_opt_in_adds_exactly_the_two_playback_scopes() -> None:
    assert scope_for(preview=True) == PREVIEW_SCOPE
    asked = scope_of(url(scope=scope_for(preview=True))).split()
    assert asked == [*SCOPE.split(), "user-modify-playback-state", "user-read-playback-state"]


@pytest.mark.parametrize(
    "scope", ["", "user-read-private", f"{SCOPE} user-read-email", f"{PREVIEW_SCOPE} streaming"]
)
def test_authorize_url_refuses_any_other_scope(scope: str) -> None:
    with pytest.raises(ValueError, match="not a scope of this flow"):
        url(scope=scope)


def test_scope_granted_is_unchanged_by_the_preview_scopes() -> None:
    assert scope_granted(SCOPE)
    assert scope_granted(PREVIEW_SCOPE)
    assert scope_granted("playlist-read-private playlist-modify-private")
    assert not scope_granted(" ".join(PREVIEW_SCOPES))


@pytest.mark.parametrize(
    ("granted", "kept"),
    [
        (SCOPE, BASE),
        (PREVIEW_SCOPE, PREVIEW),
        # Order and spacing from Spotify do not matter; unknown names are dropped.
        (f"user-read-playback-state  streaming {SCOPE} user-modify-playback-state", PREVIEW),
        (f"{SCOPE} user-read-email ugc-image-upload", BASE),
        ("", ""),
    ],
)
def test_stored_scopes_keeps_the_known_names_sorted(granted: str, kept: str) -> None:
    assert stored_scopes(granted) == kept


@pytest.mark.parametrize(
    ("stored", "granted"),
    [
        (None, False),  # a row from before migration 0070
        ("", False),
        (BASE, False),
        (f"{BASE} user-modify-playback-state", False),
        (f"{BASE} user-read-playback-state", False),
        (PREVIEW, True),
        ("user-modify-playback-state user-read-playback-state", True),
    ],
)
def test_preview_granted_needs_both_playback_scopes(stored: str | None, granted: bool) -> None:
    assert preview_granted(stored) is granted
