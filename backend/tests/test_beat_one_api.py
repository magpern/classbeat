"""Beat-one corrections through the class API (ADR 0007 amendment "beat-one corrections",
item 7, BC4): set, replace and clear one song's correction of the current revision's music, every
refusal, another user's class, and the ``beat_one`` block of the class detail. The class is
designed through the jobs API on the fake MOWL; never the network."""

from typing import Any
from uuid import UUID

import httpx
import pytest

from jobapi import Browser
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")


async def put(browser: Browser, class_id: object, song: object, body: Any) -> httpx.Response:
    headers = await browser.headers()
    made = await browser.client.put(
        f"/api/classes/{class_id}/beat-one/{song}", headers=headers, json=body
    )
    browser.client.cookies.clear()
    return made


async def delete(browser: Browser, class_id: object, song: object, revision: object):
    headers = await browser.headers()
    made = await browser.client.delete(
        f"/api/classes/{class_id}/beat-one/{song}",
        headers=headers,
        params={"revision_id": str(revision)},
    )
    browser.client.cookies.clear()
    return made


def rows(renv: ReviseEnv, class_id: UUID) -> list[tuple[str, int, int]]:
    found = renv.sql(
        "SELECT fingerprint, song, beats FROM beat_one_corrections WHERE class_id = :c "
        "ORDER BY song",
        c=class_id,
    ).all()
    return [(r.fingerprint, r.song, r.beats) for r in found]


async def test_set_replace_and_clear_one_songs_correction(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    detail = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]
    assert detail["available"] is True
    assert detail["revision_id"] == str(base)
    assert (detail["pending"], detail["stale"]) == (False, 0)
    first = detail["songs"][0]
    assert (first["song"], first["beats"], first["applied"]) == (1, 0, 0)
    assert first["title"]

    made = await put(renv.browser, class_id, 1, {"beats": 2, "revision_id": str(base)})
    assert made.status_code == 200, made.text
    view = made.json()
    assert view["songs"][0]["beats"] == 2
    assert view["pending"] is True, "not applied yet: the revision has no correction"
    fingerprint = renv.revisions(class_id)[0].music["fingerprint"]
    assert rows(renv, class_id) == [(fingerprint, 1, 2)], "the revision's own fingerprint"

    again = await put(renv.browser, class_id, 1, {"beats": -1, "revision_id": str(base)})
    assert again.json()["songs"][0]["beats"] == -1
    assert rows(renv, class_id) == [(fingerprint, 1, -1)]

    cleared = await delete(renv.browser, class_id, 1, base)
    assert cleared.status_code == 200
    assert cleared.json()["pending"] is False
    assert rows(renv, class_id) == []
    twice = await delete(renv.browser, class_id, 1, base)
    assert twice.status_code == 200, "clearing is idempotent"


@pytest.mark.parametrize(
    "body",
    [{"beats": 0}, {"beats": 4}, {"beats": -4}, {"beats": 1.0}, {"beats": "1"}, {}],
)
async def test_beats_out_of_range_or_not_an_integer_are_refused(
    renv: ReviseEnv, body: dict[str, Any]
) -> None:
    class_id, base = await renv.design()
    made = await put(renv.browser, class_id, 1, {**body, "revision_id": str(base)})
    assert made.status_code == 422
    assert rows(renv, class_id) == []


async def test_a_song_the_music_lacks_a_stale_revision_and_an_old_revision_are_refused(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    songs = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]["songs"]
    missing = await put(
        renv.browser, class_id, len(songs) + 1, {"beats": 1, "revision_id": str(base)}
    )
    assert (missing.status_code, missing.json()["detail"]["reason"]) == (422, "invalid_input")
    out_of_range = await put(renv.browser, class_id, 501, {"beats": 1, "revision_id": str(base)})
    assert out_of_range.status_code == 422
    await renv.revise(class_id, base, "revise_good")
    stale = await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(base)})
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "stale_base")
    # A revision stored before beat data existed (no ``songs``): no control, no write.
    renv.sql(
        "UPDATE class_revisions SET music = music - 'songs' WHERE id = :r",
        r=renv.current(class_id),
    )
    detail = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]
    assert detail["available"] is False
    current = renv.current(class_id)
    none = await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(current)})
    assert (none.status_code, none.json()["detail"]["reason"]) == (409, "no_beat_data")
    assert rows(renv, class_id) == []


async def test_another_users_class_is_not_found_and_unchanged(
    renv: ReviseEnv, browsers: Any
) -> None:
    class_id, base = await renv.design()
    await put(renv.browser, class_id, 1, {"beats": 2, "revision_id": str(base)})
    other: Browser = await browsers()
    made = await put(other, class_id, 1, {"beats": -3, "revision_id": str(base)})
    assert made.status_code == 404
    gone = await delete(other, class_id, 1, base)
    assert gone.status_code == 404
    assert [(s, b) for _, s, b in rows(renv, class_id)] == [(1, 2)]
    seen = await other.get(f"/api/classes/{class_id}")
    assert seen.status_code == 404


async def test_rows_for_other_music_are_counted_as_stale_and_not_shown(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats) "
        "VALUES (:o, :c, :f, 1, 3)",
        o=renv.owner, c=class_id, f="a" * 64,
    )  # fmt: skip
    detail = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]
    assert detail["stale"] == 1
    assert detail["pending"] is False
    assert all(s["beats"] == 0 for s in detail["songs"])
    assert base


async def test_a_revision_of_another_class_is_not_found(renv: ReviseEnv) -> None:
    class_id, _ = await renv.design()
    _, other_base = await renv.design()
    made = await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(other_base)})
    assert made.status_code == 404
    gone = await delete(renv.browser, class_id, 1, other_base)
    assert gone.status_code == 404
    assert rows(renv, class_id) == []
