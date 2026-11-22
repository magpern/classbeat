"""The path guard of the listening preview (ADR 0042 section 7.2, amending ADR 0009 section 10;
#1395). The Spotify allowlist is per host, so this adds what it cannot say:

- **statically**, over the repository's own source: no string names a Spotify player path but
  the four of ``player_policy.PLAYER_CALLS``, and only ``classbeat.spotify.player_policy`` names
  them (a ``PlayerCall`` is built nowhere else). ``violations`` is the check; it is shown to fail
  on each forbidden shape;
- **at run time**: ``SpotifyPlayerHttp`` sends each of the four calls through the existing
  Spotify client and its allowlist (to the in-process fake, never the network), and refuses any
  other method, path or query with ``OutboundBlocked`` before anything is sent.

Like the other guards, the static check catches accidental use and makes the rest visible in
review; it cannot stop obfuscated code.
"""

import ast
import re
from pathlib import Path

import pytest

from classbeat import egress
from classbeat.spotify.player_http import SpotifyPlayerHttp
from classbeat.spotify.player_policy import DEVICES, PAUSE, PLAY, STATE, PlayerCall
from spotifycore import API_HOST, FakeSpotify, track_id
from spotifyfake import CLIENT_A, USER_A, spotify_client

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"
POLICY = "classbeat.spotify.player_policy"
ALLOWED = frozenset(
    {"/v1/me/player", "/v1/me/player/play", "/v1/me/player/pause", "/v1/me/player/devices"}
)
# A player path in any case or spelling: ``me/player`` as a path segment pair.
PLAYER = re.compile(r"me/+player", re.IGNORECASE)


def _module(path: Path) -> str:
    parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _strings(tree: ast.AST) -> list[str]:
    """Every string constant that is not a docstring (f-string parts included)."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _builds_a_call(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func
            called = name.id if isinstance(name, ast.Name) else getattr(name, "attr", None)
            if called == "PlayerCall":
                return True
    return False


def violations(sources: list[tuple[str, ast.Module]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for module, tree in sources:
        for value in _strings(tree):
            if PLAYER.search(value) and (module != POLICY or value not in ALLOWED):
                found.append((module, value))
        if module != POLICY and _builds_a_call(tree):
            found.append((module, "PlayerCall(...)"))
    return found


def _sources() -> list[tuple[str, ast.Module]]:
    paths = sorted(SRC.rglob("*.py"))
    return [(_module(p), ast.parse(p.read_text(encoding="utf-8"))) for p in paths]


def test_classbeat_names_no_player_path_but_the_four() -> None:
    assert violations(_sources()) == []


def test_the_policy_names_exactly_the_four() -> None:
    (tree,) = [t for m, t in _sources() if m == POLICY]
    assert {v for v in _strings(tree) if PLAYER.search(v)} == ALLOWED


@pytest.mark.parametrize(
    ("module", "source"),
    [
        (POLICY, 'P = "/v1/me/player/next"'),
        (POLICY, 'P = "/v1/me/player/seek"'),
        (POLICY, 'P = "/v1/me/player/queue"'),
        (POLICY, 'P = "/v1/me/player/volume"'),
        (POLICY, 'P = "/v1/me/player/play/"'),
        (POLICY, 'P = "/v1/me/Player/play"'),
        (POLICY, 'P = "/v1/me//player/play"'),
        (POLICY, 'P = "v1/me/player/play"'),
        (POLICY, 'P = "/v1/me/player/play?device_id=x"'),
        (POLICY, 'U = "https://api.spotify.com/v1/me/player/play"'),
        ("classbeat.spotify.player_http", 'P = "/v1/me/player/play"'),
        ("classbeat.classes.preview_spotify", 'P = "/v1/me/player"'),
        ("classbeat.anything", 'P = f"/v1/me/player/{x}"'),
        ("classbeat.anything", 'C = PlayerCall("POST", p)'),
        ("classbeat.anything", 'C = policy.PlayerCall("PUT", p)'),
    ],
)
def test_the_guard_fails_on_a_forbidden_path(module: str, source: str) -> None:
    assert violations([(module, ast.parse(source))]) != []


@pytest.mark.parametrize(
    "source", ['P = "/v1/me/player"', 'P = "/v1/me/player/devices"', 'C = PlayerCall("PUT", P)']
)
def test_the_guard_passes_the_policy_itself(source: str) -> None:
    assert violations([(POLICY, ast.parse(source))]) == []


# --- at run time ---------------------------------------------------------------------------


def player(fake: FakeSpotify) -> SpotifyPlayerHttp:
    return SpotifyPlayerHttp(lambda total: spotify_client(fake, total_seconds=total))


async def test_each_call_goes_through_the_spotify_client_to_its_exact_path(
    spotify: FakeSpotify,
) -> None:
    access, _ = spotify.grant(
        CLIENT_A, USER_A, "user-read-playback-state user-modify-playback-state"
    )
    spotify.add_device(USER_A, "d" * 40, active=True)
    http = player(spotify)
    body = {"uris": [f"spotify:track:{track_id(1)}"], "position_ms": 0}
    assert (await http.send(PLAY, access, device_id="d" * 40, body=body)).status == 204
    assert (await http.send(STATE, access)).status == 200
    assert (await http.send(DEVICES, access)).status == 200
    assert (await http.send(PAUSE, access)).status == 204
    seen = spotify.requests(API_HOST)
    assert [(s.method, s.path) for s in seen] == [
        ("PUT", "/v1/me/player/play"),
        ("GET", "/v1/me/player"),
        ("GET", "/v1/me/player/devices"),
        ("PUT", "/v1/me/player/pause"),
    ]
    assert seen[0].query == {"device_id": "d" * 40}
    assert all(s.query == {} for s in seen[1:])


@pytest.mark.parametrize(
    "call",
    [
        PlayerCall("POST", "/v1/me/player/next"),
        PlayerCall("PUT", "/v1/me/player/seek"),
        PlayerCall("POST", "/v1/me/player/queue"),
        PlayerCall("PUT", "/v1/me/player"),  # a transfer to another device
        PlayerCall("GET", "/v1/me/player/play"),
        PlayerCall("PUT", "/v1/me/player/play?context_uri=spotify:album:x"),
        PlayerCall("GET", "/v1/me/player/../player/queue"),
        PlayerCall("GET", "/v1/me/playlists"),
        PlayerCall("GET", "/v1/me"),
    ],
)
async def test_any_other_call_is_refused_before_it_is_sent(
    spotify: FakeSpotify, call: PlayerCall
) -> None:
    with pytest.raises(egress.OutboundBlocked):
        await player(spotify).send(call, "fake-at-x")
    assert spotify.requests() == []


async def test_a_pause_never_carries_a_device(spotify: FakeSpotify) -> None:
    with pytest.raises(egress.OutboundBlocked):
        await player(spotify).send(PAUSE, "fake-at-x", device_id="d" * 40)
    assert spotify.requests() == []
