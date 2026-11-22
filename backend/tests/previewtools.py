"""Helpers for the listening-preview tests (ADR 0042 section 7, #1395): a user with a Spotify
connection that holds (or lacks) the preview scopes, and a class whose current draft names an
approved song list. TEST ONLY, never the network: the in-process fake Spotify answers both the
token client and the player client through the real Spotify allowlist; public test values only."""

import time
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat import connections
from classbeat.songs.song_list_model import content_hash, fingerprint
from classbeat.spotify.flow import stored_scopes
from classbeat.spotify.player_http import SpotifyPlayerHttp
from ctx import make_context
from jobtools import digest
from mfatools import Actor
from spotifycore import SCOPES, track_id
from spotifyfake import CLIENT_A, USER_A, spotify_client
from spotifytools import Spot
from test_classes_schema import insert

PREVIEW_SCOPES = f"{SCOPES} user-modify-playback-state user-read-playback-state"
TRACKS = (track_id(1), track_id(2), track_id(1))  # the first song plays twice
LENGTH_SEC = 200
DEVICE = "d1" * 20  # 40 characters, as Spotify's device ids are
BASE = "/api/classes/{}/preview/spotify"


def install_player(spot: Spot) -> None:
    spot.env.app.state.spotify_player_http = SpotifyPlayerHttp(
        lambda total: spotify_client(spot.fake, total_seconds=total)
    )


async def seed_connection(
    spot: Spot, owner: UUID, *, scope: str = PREVIEW_SCOPES, expires_in: float = 3000
) -> tuple[str, str]:
    """A connected Spotify row of user A on app A, with the grant ``scope`` stored."""
    access, refresh = spot.fake.grant(CLIENT_A, USER_A, scope)
    expires_at = int(time.time() + expires_in)
    await connections.save(
        spot.env.factory,
        spot.ring,
        make_context(owner),
        "spotify",
        {"access_token": access, "refresh_token": refresh, "expires_at": expires_at},
        public={"client_id": CLIENT_A, "spotify_user_id": USER_A},
        confirm_account_change=True,
        granted_scopes=stored_scopes(scope),
    )
    return access, refresh


def _song(tid: str) -> dict[str, Any]:
    return {
        "spotify_id": tid, "title": "Song", "artist": "Artist", "role": "climb", "bpm": 120.0,
        "length_sec": LENGTH_SEC, "new": False,
    }  # fmt: skip


def song_list(admin: Connection, owner: UUID, class_id: UUID, tracks: tuple[str, ...]) -> UUID:
    content = {
        "v": 1, "mode": "pick", "target_sec": 600, "total_sec": 600, "crossfade_sec": 9.0,
        "songs": [_song(t) for t in tracks], "omitted_no_bpm": 0, "note": "",
    }  # fmt: skip
    music = {"source": "claude_pick", "fingerprint": fingerprint(tracks), "spotify_user_id": USER_A}
    return insert(
        admin, "class_revisions", owner_id=owner, class_id=class_id, number=1, kind="song_list",
        content=content, content_hash=content_hash(content), music=music, provenance={},
    )  # fmt: skip


def draft(admin: Connection, owner: UUID, class_id: UUID, list_id: UUID | None) -> UUID:
    music: dict[str, Any] = {
        "source": "mowl_spotify_playlist", "link_id": str(uuid.uuid4()), "fingerprint": "a" * 64,
        "unknown_bpm": [],
    }  # fmt: skip
    if list_id is not None:
        music["song_list_id"] = str(list_id)
    return insert(
        admin, "class_revisions", owner_id=owner, class_id=class_id, number=2, kind="draft",
        content={"intervals": []}, content_hash=digest(f"draft-{class_id}"), music=music,
        provenance={},
    )  # fmt: skip


def new_class(admin: Connection, owner: UUID) -> UUID:
    class_id = uuid.uuid4()
    admin.execute(
        text("INSERT INTO classes (id, owner_id, title) VALUES (:id, :o, 'Preview climb')"),
        {"id": class_id, "o": owner},
    )
    return class_id


def make_current(admin: Connection, class_id: UUID, revision_id: UUID) -> None:
    admin.execute(
        text("UPDATE classes SET current_revision_id = :r WHERE id = :c"),
        {"r": revision_id, "c": class_id},
    )


def class_with_draft(admin: Connection, owner: UUID, tracks: tuple[str, ...] = TRACKS) -> UUID:
    class_id = new_class(admin, owner)
    list_id = song_list(admin, owner, class_id, tracks)
    make_current(admin, class_id, draft(admin, owner, class_id, list_id))
    return class_id


@dataclass
class Preview:
    spot: Spot
    admin: Connection
    actor: Actor
    owner: UUID
    class_id: UUID
    access: str
    refresh: str

    def url(self, what: str, class_id: UUID | str | None = None) -> str:
        return f"{BASE.format(class_id or self.class_id)}/{what}"

    async def audition(self, body: Any, class_id: UUID | str | None = None) -> Any:
        return await self.actor.post(self.url("audition", class_id), body)

    async def get(self, what: str, class_id: UUID | str | None = None) -> Any:
        return await self.actor.get(self.url(what, class_id))

    def connection(self) -> Any:
        return self.admin.execute(
            text(
                "SELECT status, last_error, last_used_at FROM connections "
                "WHERE owner_id = :o AND provider = 'spotify'"
            ),
            {"o": self.owner},
        ).one()


async def preview(spot: Spot, admin: Connection, **connection: Any) -> Preview:
    install_player(spot)
    actor = await spot.actor(f"preview-{uuid.uuid4().hex[:8]}@example.com")
    owner = spot.user_id(actor)
    access, refresh = await seed_connection(spot, owner, **connection)
    return Preview(spot, admin, actor, owner, class_with_draft(admin, owner), access, refresh)
