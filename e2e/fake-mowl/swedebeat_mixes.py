"""The Swedebeat calls of the fake MOWL (ADR 0016, A1). TEST ONLY, invented data.

What the library makes (`mowl/api.py`): `POST v1/swedebeat/Playlists` (the caller's mixes),
`PUT v1/swedebeat/Playlist` (attach: a MOWL playlist, the same one on a repeat),
`GET v1/Playlists/<id>/tracks` (indexing, done at the first read) and
`GET v1/swedebeat/playlist/<id>/TemporaryLinks`. The fake serves no audio: the link it returns names
`UNLISTED_MEDIA_HOST`, a host no list holds and no service answers, so the production (empty)
`MOWL_MEDIA_HOSTS` must refuse it (fail-closed e2e, A6). The link is minted per request with a
random signature, never logged or recorded; `state()` counts what was asked, never a token, link or
signature. Every mix is listed to every signed-in account; ownership is the attach by token.
"""

import secrets
import threading

UNLISTED_MEDIA_HOST = "media.unlisted.example"
LOCK = threading.Lock()
MIXES = [
    {"ID": "e2e-mix-0001", "Name": "E2E Swedebeat Mix", "DurationInMilliseconds": 120_000},
    {"ID": "e2e-mix-0002", "Name": "E2E Cached Mix", "DurationInMilliseconds": 90_000},
]
FIRST_PLAYLIST = 9001
ATTACHED: dict[tuple[str, str], int] = {}  # (token, mix id) -> MOWL playlist id
COUNTS = {"lists": 0, "attaches": 0, "links": 0}


def listing(token: str, form: dict[str, str]) -> dict[str, object]:
    """The mixes, each with the `PlaylistID` of the caller's attach of it once there is one (what the
    attach reconciler reads)."""
    like = form.get("NameLike", "").lower()
    with LOCK:
        COUNTS["lists"] += 1
        shown: list[dict[str, object]] = []
        for m in MIXES:
            if like not in str(m["Name"]).lower():
                continue
            entry = dict(m, URL=f"mix:{m['ID']}")
            if (token, str(m["ID"])) in ATTACHED:
                entry["PlaylistID"] = ATTACHED[(token, str(m["ID"]))]
            shown.append(entry)
    return {"Playlists": shown}


def attach(token: str, form: dict[str, str]) -> dict[str, object] | None:
    mix = next((m for m in MIXES if m["ID"] == form.get("SwedebeatPlaylistID")), None)
    if mix is None:
        return None
    with LOCK:
        COUNTS["attaches"] += 1
        pid = ATTACHED.setdefault((token, str(mix["ID"])), FIRST_PLAYLIST + len(ATTACHED))
    return {"PlaylistID": pid, "PlaylistName": mix["Name"]}


def _mix_of(token: str, playlist_id: int) -> dict[str, object] | None:
    for (owner, mix_id), pid in ATTACHED.items():
        if owner == token and pid == playlist_id:
            return next(m for m in MIXES if m["ID"] == mix_id)
    return None


def tracks(token: str, playlist_id: int) -> list[dict[str, object]] | None:
    with LOCK:
        mix = _mix_of(token, playlist_id)
    if mix is None:
        return None
    return [
        {
            "TrackID": 70_000_000 + playlist_id,
            "Duration": mix["DurationInMilliseconds"],
            "IndexStatus": "Track indexing done",
            "IndexError": None,
        }
    ]


def links(token: str, playlist_id: int) -> dict[str, object] | None:
    with LOCK:
        mix = _mix_of(token, playlist_id)
        if mix is None:
            return None
        COUNTS["links"] += 1
    link = f"https://{UNLISTED_MEDIA_HOST}/media/{secrets.token_hex(8)}/mix.mp3?sig={secrets.token_hex(16)}"
    return {"TemporaryLinks": [{"TemporaryLink": link}]}


def reset() -> None:
    with LOCK:
        ATTACHED.clear()
        COUNTS.update(lists=0, attaches=0, links=0)


def state() -> dict[str, object]:
    with LOCK:
        return {**COUNTS, "attached": len(ATTACHED)}
