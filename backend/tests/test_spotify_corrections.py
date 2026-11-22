"""The Spotify draft's alignment facts and the instructor's corrections through the API (ADR 0042
sections 2 and 7.4, #1398). Never the network: no Spotify request is made at all.

Covered: ``GET .../alignment`` gives each song's length and spotdown's alignment with the class's
correction beside it, never merged; a correction is stored against the analysis now (its
``map_version`` and the analysed file's sha256), never edits an analysis or a revision, and never
reaches another class of the user on the same track; another user's class is 404; ``listen_again``
after a re-analysis (another sha256 or ``map_version``), until it is set again; the refusals
(``no_draft``, ``stale_base``, ``invalid_input``, ``no_analysis``, ``correction_limit``); and no
Spotify id, token or URL in any answer."""

import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobtools import digest
from previewtools import TRACKS, Preview, class_with_draft, make_current, new_class, preview
from spotifytools import Spot
from test_classes_schema import insert

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

SHAS = ("a1" * 32, "b2" * 32, "c3" * 32)
BASE = "/api/classes/{}/preview/spotify"


def _entry(position: int, sha: str, offset_s: float | None, cls: str = "approximate") -> dict:
    evidence: dict[str, Any] = {"alignment": "ok", "offset_s": offset_s}
    if offset_s is None:
        evidence = {"alignment": "failed"}
    return {
        "position": position, "use": "spotdown", "analysis_id": f"analysis-{position}",
        "audio": {"sha256": sha}, "timing": {"class": cls, "evidence": evidence},
    }  # fmt: skip


def analyse(admin: Connection, class_id: UUID, entries: list[dict]) -> None:
    """The class's song list records spotdown's analysis of each song (its provenance)."""
    admin.execute(
        text(
            "UPDATE class_revisions SET provenance = CAST(:p AS jsonb) "
            "WHERE class_id = :c AND kind = 'song_list'"
        ),
        {"p": json.dumps({"spotdown_analysis": {"v": 2, "songs": entries}}), "c": class_id},
    )


def stamp_draft(admin: Connection, class_id: UUID, **provenance: Any) -> None:
    admin.execute(
        text(
            "UPDATE class_revisions SET provenance = CAST(:p AS jsonb) "
            "WHERE class_id = :c AND kind = 'draft'"
        ),
        {"p": json.dumps(provenance), "c": class_id},
    )


def current_revision(admin: Connection, class_id: UUID) -> UUID:
    return admin.execute(
        text("SELECT current_revision_id FROM classes WHERE id = :c"), {"c": class_id}
    ).scalar_one()


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


def entries() -> list[dict]:
    return [_entry(0, SHAS[0], 1.25), _entry(1, SHAS[1], -0.5, "unusable"), _entry(2, SHAS[2], 0.0)]


@pytest.fixture
async def pv(spot: Spot, spotify_admin: Connection) -> Preview:
    made = await preview(spot, spotify_admin)
    analyse(spotify_admin, made.class_id, entries())
    stamp_draft(spotify_admin, made.class_id, map_version=3, crossfade_sec=9.0)
    return made


def url(pv: Preview, what: str, class_id: UUID | str | None = None) -> str:
    return f"{BASE.format(class_id or pv.class_id)}/{what}"


async def put(pv: Preview, song: int, offset_ms: int, **more: Any) -> Any:
    body = {"revision_id": str(current_revision(pv.admin, pv.class_id)), "offset_ms": offset_ms}
    return await pv.actor.call(
        "PUT", url(pv, f"corrections/{song}", more.pop("class_id", None)), body | more
    )


async def view(pv: Preview, class_id: UUID | str | None = None) -> dict[str, Any]:
    response = await pv.actor.get(url(pv, "alignment", class_id))
    assert response.status_code == 200, response.text
    return response.json()


# --- the view ------------------------------------------------------------------------------


async def test_the_view_gives_each_songs_length_and_spotdowns_alignment_with_no_correction(
    pv: Preview,
) -> None:
    body = await view(pv)
    assert body["revision_id"] == str(current_revision(pv.admin, pv.class_id))
    assert (body["map_version"], body["crossfade_ms"]) == (3, 9000)
    first, second, third = body["songs"]
    assert (first["piece"], first["song"], first["length_ms"]) == (0, 1, 200_000)
    assert first["analysis"] == {
        "spotdown_job": "analysis-0", "sha256": SHAS[0], "timing_class": "approximate"
    }  # fmt: skip
    assert first["alignment"] == {"offset_ms": 1250, "source": "spotdown"}
    assert second["analysis"]["timing_class"] == "unusable"
    assert second["alignment"]["offset_ms"] == -500
    assert third["alignment"]["offset_ms"] == 0
    assert [s["correction"] for s in body["songs"]] == [None, None, None]


async def test_a_song_without_a_record_has_no_alignment_and_the_crossfade_falls_back_to_the_list(
    pv: Preview,
) -> None:
    analyse(pv.admin, pv.class_id, [_entry(0, SHAS[0], 1.0), _entry(1, SHAS[1], None)])
    stamp_draft(pv.admin, pv.class_id)  # a draft with no ``crossfade_sec``: the list's is 9.0
    body = await view(pv)
    assert body["crossfade_ms"] == 9000
    assert body["map_version"] is None
    one, two, three = body["songs"]
    assert one["alignment"]["offset_ms"] == 1000
    assert two["alignment"]["offset_ms"] is None  # its alignment failed
    assert three["analysis"] == {"spotdown_job": None, "sha256": None, "timing_class": None}
    assert three["alignment"]["offset_ms"] is None


async def test_nothing_in_an_answer_names_a_spotify_track_or_holds_a_secret(pv: Preview) -> None:
    await put(pv, 1, 300)
    for response in (
        await pv.actor.get(url(pv, "alignment")),
        await put(pv, 1, 400),
    ):
        text_ = response.text
        assert all(track not in text_ for track in TRACKS)
        assert pv.access not in text_
        assert pv.refresh not in text_
        assert "spotify:" not in text_
        assert "http" not in text_
        assert response.headers["cache-control"] == "no-store"


# --- a correction --------------------------------------------------------------------------


async def test_a_correction_is_stored_against_the_analysis_and_kept_beside_it(
    pv: Preview,
) -> None:
    before = await view(pv)
    revisions = pv.admin.execute(
        text("SELECT id, content_hash, music, provenance FROM class_revisions WHERE class_id = :c"),
        {"c": pv.class_id},
    ).all()
    response = await put(pv, 1, -350)
    assert response.status_code == 200, response.text
    song = response.json()["songs"][0]
    # The correction, with what it was set against and that it holds:
    assert song["correction"] == {
        "offset_ms": -350,
        "set_against_analysis": {"map_version": 3, "sha256": SHAS[0]},
        "state": "current",
    }
    # spotdown's facts are exactly what they were: the two are never merged.
    assert song["analysis"] == before["songs"][0]["analysis"]
    assert (
        song["alignment"]
        == before["songs"][0]["alignment"]
        == {"offset_ms": 1250, "source": "spotdown"}
    )
    assert (await view(pv))["songs"][0]["correction"]["offset_ms"] == -350
    # It only moves the audition cursor: no revision, analysis or pointer changed.
    after = pv.admin.execute(
        text("SELECT id, content_hash, music, provenance FROM class_revisions WHERE class_id = :c"),
        {"c": pv.class_id},
    ).all()
    assert sorted(map(tuple, after), key=str) == sorted(map(tuple, revisions), key=str)
    row = pv.admin.execute(
        text("SELECT song, offset_ms, set_against_analysis FROM spotify_offset_corrections")
    ).one()
    assert tuple(row) == (1, -350, {"map_version": 3, "sha256": SHAS[0]})


async def test_setting_it_again_replaces_it_and_clearing_removes_it_idempotently(
    pv: Preview,
) -> None:
    await put(pv, 2, 100)
    again = await put(pv, 2, 5_000)
    assert again.json()["songs"][1]["correction"]["offset_ms"] == 5_000
    count = text("SELECT count(*) FROM spotify_offset_corrections")
    assert pv.admin.execute(count).scalar_one() == 1
    revision = str(current_revision(pv.admin, pv.class_id))
    for _ in range(2):  # clearing twice is the same
        cleared = await pv.actor.call("DELETE", url(pv, f"corrections/2?revision_id={revision}"))
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["songs"][1]["correction"] is None
    assert pv.admin.execute(count).scalar_one() == 0


async def test_a_correction_never_reaches_another_class_of_the_user_on_the_same_track(
    pv: Preview,
) -> None:
    other = class_with_draft(pv.admin, pv.owner)  # the same tracks, the same fingerprint
    analyse(pv.admin, other, entries())
    stamp_draft(pv.admin, other, map_version=3, crossfade_sec=9.0)
    assert (await put(pv, 1, 700)).status_code == 200
    assert (await view(pv))["songs"][0]["correction"]["offset_ms"] == 700
    assert [s["correction"] for s in (await view(pv, other))["songs"]] == [None, None, None]
    # and a write to the other class is its own row
    revision = str(current_revision(pv.admin, other))
    response = await pv.actor.call(
        "PUT", url(pv, "corrections/1", other), {"revision_id": revision, "offset_ms": -9}
    )
    assert response.status_code == 200
    assert (await view(pv))["songs"][0]["correction"]["offset_ms"] == 700


async def test_another_users_class_is_not_found_for_every_route(
    pv: Preview, spot: Spot, spotify_admin: Connection
) -> None:
    stranger = await spot.actor(f"stranger-{uuid.uuid4().hex[:8]}@example.com")
    theirs = pv.class_id
    revision = str(current_revision(pv.admin, theirs))
    assert (await stranger.get(url(pv, "alignment", theirs))).status_code == 404
    body = {"revision_id": revision, "offset_ms": 100}
    assert (await stranger.call("PUT", url(pv, "corrections/1", theirs), body)).status_code == 404
    gone = await stranger.call("DELETE", url(pv, f"corrections/1?revision_id={revision}", theirs))
    assert gone.status_code == 404
    assert (await stranger.get(url(pv, "alignment", "not-a-uuid"))).status_code == 404
    assert (
        spotify_admin.execute(text("SELECT count(*) FROM spotify_offset_corrections")).scalar_one()
        == 0
    )


async def test_another_users_rows_are_never_read_or_cleared(
    pv: Preview, spot: Spot, spotify_admin: Connection
) -> None:
    assert (await put(pv, 1, 700)).status_code == 200
    stranger = await preview(spot, spotify_admin)  # their own class with the same music
    analyse(spotify_admin, stranger.class_id, entries())
    assert [s["correction"] for s in (await view(stranger))["songs"]] == [None, None, None]
    revision = str(current_revision(spotify_admin, stranger.class_id))
    cleared = await stranger.actor.call(
        "DELETE", url(stranger, f"corrections/1?revision_id={revision}")
    )
    assert cleared.status_code == 200
    assert (await view(pv))["songs"][0]["correction"]["offset_ms"] == 700


# --- listen again --------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["sha256", "map_version"])
async def test_a_changed_analysis_makes_the_correction_listen_again_until_it_is_set_again(
    pv: Preview, change: str
) -> None:
    assert (await put(pv, 1, 700)).status_code == 200
    if change == "sha256":
        analyse(pv.admin, pv.class_id, [_entry(0, "f0" * 32, 1.25), *entries()[1:]])
    else:
        stamp_draft(pv.admin, pv.class_id, map_version=4, crossfade_sec=9.0)
    song = (await view(pv))["songs"][0]
    # Shown, with what it was set against, but not applied:
    assert song["correction"]["state"] == "listen_again"
    assert song["correction"]["offset_ms"] == 700
    assert song["correction"]["set_against_analysis"] == {"map_version": 3, "sha256": SHAS[0]}
    confirmed = (await put(pv, 1, 700)).json()["songs"][0]["correction"]
    assert confirmed["state"] == "current"
    assert confirmed["set_against_analysis"] != {"map_version": 3, "sha256": SHAS[0]}


async def test_a_correction_whose_analysis_is_gone_is_listen_again(pv: Preview) -> None:
    assert (await put(pv, 1, 700)).status_code == 200
    analyse(pv.admin, pv.class_id, [])
    song = (await view(pv))["songs"][0]
    assert song["correction"]["state"] == "listen_again"
    assert song["alignment"]["offset_ms"] is None


# --- refusals ------------------------------------------------------------------------------


async def test_a_song_with_no_analysed_file_takes_no_correction(pv: Preview) -> None:
    analyse(pv.admin, pv.class_id, [_entry(0, SHAS[0], 1.0)])
    response = await put(pv, 2, 100)
    assert (response.status_code, reason(response)) == (409, "no_analysis")
    assert (
        pv.admin.execute(text("SELECT count(*) FROM spotify_offset_corrections")).scalar_one() == 0
    )


async def test_a_draft_that_is_not_current_any_more_is_stale_base(pv: Preview) -> None:
    old = current_revision(pv.admin, pv.class_id)
    newer = insert(
        pv.admin, "class_revisions", owner_id=pv.owner, class_id=pv.class_id, number=3,
        kind="draft", content={"intervals": []}, content_hash=digest("newer"), provenance={},
        music=pv.admin.execute(
            text("SELECT music FROM class_revisions WHERE id = :r"), {"r": old}
        ).scalar_one(),
    )  # fmt: skip
    make_current(pv.admin, pv.class_id, newer)
    response = await pv.actor.call(
        "PUT", url(pv, "corrections/1"), {"revision_id": str(old), "offset_ms": 100}
    )
    assert (response.status_code, reason(response)) == (409, "stale_base")
    cleared = await pv.actor.call("DELETE", url(pv, f"corrections/1?revision_id={old}"))
    assert (cleared.status_code, reason(cleared)) == (409, "stale_base")


async def test_a_revision_that_is_not_the_classs_own_is_not_found(pv: Preview) -> None:
    body = {"revision_id": str(uuid.uuid4()), "offset_ms": 100}
    response = await pv.actor.call("PUT", url(pv, "corrections/1"), body)
    assert (response.status_code, reason(response)) == (404, "not_found")
    cleared = await pv.actor.call("DELETE", url(pv, f"corrections/1?revision_id={uuid.uuid4()}"))
    assert cleared.status_code == 404


@pytest.mark.parametrize(
    ("song", "offset", "status"),
    [(4, 100, 422), (0, 100, 422), (201, 100, 422), (1, 0, 422), (1, 60_001, 422), (1, "1", 422)],
)
async def test_a_song_the_draft_does_not_play_and_an_offset_out_of_range_are_refused(
    pv: Preview, song: int, offset: Any, status: int
) -> None:
    response = await put(pv, song, offset)
    assert response.status_code == status, response.text
    assert (
        pv.admin.execute(text("SELECT count(*) FROM spotify_offset_corrections")).scalar_one() == 0
    )


async def test_the_body_takes_nothing_else(pv: Preview) -> None:
    response = await put(pv, 1, 100, fingerprint="a" * 64)
    assert response.status_code == 422
    response = await put(pv, 1, 100, set_against_analysis={"map_version": 1, "sha256": SHAS[0]})
    assert response.status_code == 422


async def test_a_class_with_no_current_draft_has_no_view_and_takes_no_correction(
    pv: Preview, spotify_admin: Connection
) -> None:
    empty = new_class(spotify_admin, pv.owner)
    response = await pv.actor.get(url(pv, "alignment", empty))
    assert (response.status_code, reason(response)) == (409, "no_draft")
    body = {"revision_id": str(uuid.uuid4()), "offset_ms": 100}
    # no revision to write against: not one of the class's own
    assert (await pv.actor.call("PUT", url(pv, "corrections/1", empty), body)).status_code == 404
    # a draft with no approved song list (a MOWL-link class) is the same
    unlisted = class_with_draft(spotify_admin, pv.owner)
    spotify_admin.execute(
        text("UPDATE class_revisions SET music = music - 'song_list_id' WHERE class_id = :c"),
        {"c": unlisted},
    )
    response = await pv.actor.get(url(pv, "alignment", unlisted))
    assert (response.status_code, reason(response)) == (409, "no_draft")
    make_current(spotify_admin, unlisted, current_revision(spotify_admin, unlisted))


async def test_a_class_holds_a_bounded_number_of_corrections_for_its_current_music(
    pv: Preview, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The column's song bound (1..200) and the one-per-song key already cap a class at 200 rows,
    # so the guard is shown with a lower bound: a new row is refused, replacing one is not.
    monkeypatch.setattr("classbeat.classes.spotify_corrections.MAX_ROWS", 2)
    assert (await put(pv, 1, 100)).status_code == 200
    assert (await put(pv, 2, 100)).status_code == 200
    response = await put(pv, 3, 100)
    assert (response.status_code, reason(response)) == (409, "correction_limit")
    assert (await put(pv, 2, 200)).status_code == 200
    count = pv.admin.execute(text("SELECT count(*) FROM spotify_offset_corrections")).scalar_one()
    assert count == 2


async def test_the_most_a_class_can_hold_is_one_per_song_up_to_200(pv: Preview) -> None:
    for song in range(4, 201):  # rows for songs this draft does not play
        pv.admin.execute(
            text(
                "INSERT INTO spotify_offset_corrections (owner_id, class_id, fingerprint, song, "
                "offset_ms, set_against_analysis) VALUES (:o, :c, :f, :s, 10, CAST(:a AS jsonb))"
            ),
            {
                "o": pv.owner, "c": pv.class_id, "f": "a" * 64, "s": song,
                "a": json.dumps({"map_version": None, "sha256": SHAS[0]}),
            },
        )  # fmt: skip
    for song in (1, 2, 3):
        assert (await put(pv, song, 100)).status_code == 200
    count = pv.admin.execute(text("SELECT count(*) FROM spotify_offset_corrections")).scalar_one()
    assert count == 200
