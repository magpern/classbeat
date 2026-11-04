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
    assert (detail["pending"], detail["stale"]) == (False, [])
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
    row = renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 3, 120) RETURNING id",
        o=renv.owner, c=class_id, f="a" * 64,
    ).scalar_one()  # fmt: skip
    detail = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]
    assert detail["stale"] == [{"id": str(row), "song": 1, "beats": 3}]
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


async def call(browser: Browser, method: str, path: str) -> httpx.Response:
    headers = await browser.headers()
    made = await browser.client.request(method, path, headers=headers)
    browser.client.cookies.clear()
    return made


async def test_a_stale_row_is_cleared_by_id_and_another_classes_row_is_404(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    other_class, _ = await renv.design()
    mine = renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 3, 120) RETURNING id",
        o=renv.owner, c=class_id, f="a" * 64,
    ).scalar_one()  # fmt: skip
    theirs = renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 2, 120) RETURNING id",
        o=renv.owner, c=other_class, f="a" * 64,
    ).scalar_one()  # fmt: skip
    wrong = await call(renv.browser, "DELETE", f"/api/classes/{class_id}/beat-one/rows/{theirs}")
    assert wrong.status_code == 404, "a row of another class of the same user"
    assert len(rows(renv, other_class)) == 1
    done = await call(renv.browser, "DELETE", f"/api/classes/{class_id}/beat-one/rows/{mine}")
    assert done.status_code == 200
    assert done.json()["stale"] == []
    assert rows(renv, class_id) == []
    assert base


async def test_clear_all_removes_every_row_of_the_class_only(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    other_class, other_base = await renv.design()
    await put(renv.browser, class_id, 1, {"beats": 2, "revision_id": str(base)})
    await put(renv.browser, other_class, 1, {"beats": 1, "revision_id": str(other_base)})
    renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 3, 120)",
        o=renv.owner, c=class_id, f="a" * 64,
    )  # fmt: skip
    done = await call(renv.browser, "DELETE", f"/api/classes/{class_id}/beat-one")
    assert done.status_code == 200
    assert rows(renv, class_id) == []
    assert len(rows(renv, other_class)) == 1


async def test_the_cap_counts_the_current_music_only(
    renv: ReviseEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classbeat.classes import beat_one as api_beat_one

    monkeypatch.setattr(api_beat_one, "MAX_PER_CLASS", 1)
    class_id, base = await renv.design()
    renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 3, 120), (:o, :c, :f, 2, 3, 120)",
        o=renv.owner, c=class_id, f="a" * 64,
    )  # fmt: skip
    first = await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(base)})
    assert first.status_code == 200, "stale rows do not count"
    again = await put(renv.browser, class_id, 1, {"beats": 2, "revision_id": str(base)})
    assert again.status_code == 200, "replacing a song's own row is always allowed"
    over = await put(renv.browser, class_id, 2, {"beats": 1, "revision_id": str(base)})
    assert (over.status_code, over.json()["detail"]["reason"]) == (409, "correction_limit")


@pytest.mark.parametrize(
    ("source", "reason"), [('{"source": "none"}', "no_beat_data"), (None, "not_changeable")]
)
async def test_a_soundless_class_or_one_with_no_music_source_takes_no_correction(
    renv: ReviseEnv, source: str | None, reason: str
) -> None:
    class_id, base = await renv.design()
    renv.sql(
        "UPDATE classes SET music_source = CAST(:s AS jsonb) WHERE id = :c", s=source, c=class_id
    )
    made = await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(base)})
    assert (made.status_code, made.json()["detail"]["reason"]) == (409, reason)
    detail = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]
    assert detail["available"] is False
    assert rows(renv, class_id) == []


async def test_clearing_a_song_with_a_revision_that_is_not_current_is_stale_base(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(base)})
    await renv.revise(class_id, base, "revise_good")
    made = await delete(renv.browser, class_id, 1, base)
    assert (made.status_code, made.json()["detail"]["reason"]) == (409, "stale_base")
    assert len(rows(renv, class_id)) == 1, "nothing deleted"


async def test_a_row_records_the_bpm_and_a_changed_bpm_is_flagged(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(base)})
    stored = renv.sql("SELECT bpm FROM beat_one_corrections WHERE class_id = :c", c=class_id)
    song = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]["songs"][0]
    assert stored.scalar_one() == pytest.approx(song["bpm"])
    assert song["bpm_changed"] is False
    renv.sql("UPDATE beat_one_corrections SET bpm = bpm + 10 WHERE class_id = :c", c=class_id)
    song = (await renv.browser.get(f"/api/classes/{class_id}")).json()["beat_one"]["songs"][0]
    assert song["bpm_changed"] is True


def test_bpm_changed_ignores_single_precision_and_sees_a_real_change() -> None:
    from classbeat.classes.beat_one import _bpm_changed  # pyright: ignore[reportPrivateUsage]

    assert _bpm_changed(128.30000305, 128.3) is False  # real column read back
    assert _bpm_changed(127.99999, 128.0) is False
    assert _bpm_changed(128.3, 128.4) is True
    assert _bpm_changed(120.0, 126.0) is True


async def test_clearing_a_song_leaves_the_same_song_of_another_class_on_the_same_music(
    renv: ReviseEnv,
) -> None:
    first, first_base = await renv.design()
    second, second_base = await renv.design()  # the same playlist: the same fingerprint
    await put(renv.browser, first, 1, {"beats": 2, "revision_id": str(first_base)})
    await put(renv.browser, second, 1, {"beats": -1, "revision_id": str(second_base)})
    cleared = await delete(renv.browser, first, 1, first_base)
    assert cleared.status_code == 200
    assert rows(renv, first) == []
    assert [(s, b) for _, s, b in rows(renv, second)] == [(1, -1)]


async def test_clearing_a_song_leaves_its_row_for_earlier_music(renv: ReviseEnv) -> None:
    """ADR items 2 and 9: rows for other music are kept, so Make current can bring them back."""
    class_id, base = await renv.design()
    renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 3, 120)",
        o=renv.owner, c=class_id, f="a" * 64,
    )  # fmt: skip
    await put(renv.browser, class_id, 1, {"beats": 1, "revision_id": str(base)})
    cleared = await delete(renv.browser, class_id, 1, base)
    assert cleared.status_code == 200
    assert rows(renv, class_id) == [("a" * 64, 1, 3)]


async def test_parallel_corrections_never_pass_the_cap(
    renv: ReviseEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The class row lock (``FOR UPDATE``) serializes the count and the insert: many PUTs at once
    for different songs store exactly the cap, and every answer is 200 or 409."""
    import asyncio

    from classbeat.classes import beat_one as api_beat_one

    monkeypatch.setattr(api_beat_one, "MAX_PER_CLASS", 2)
    class_id, base = await renv.design()
    songs = [s["song"] for s in renv.revisions(class_id)[0].music["songs"]]
    headers = await renv.browser.headers()
    client = renv.browser.client

    async def one(song: int) -> int:
        made = await client.put(
            f"/api/classes/{class_id}/beat-one/{song}",
            headers=headers,
            json={"beats": 1, "revision_id": str(base)},
        )
        return made.status_code

    answers = await asyncio.gather(*(one(s) for s in songs * 3))
    renv.browser.client.cookies.clear()
    assert set(answers) <= {200, 409}
    assert len(rows(renv, class_id)) == 2
