"""The composer's music answer and the song search that reads it (ADR 0038 amendment of
2026-11-24, decision 5, #1495) through ``PUT /api/classes/{id}/music``, ``fill-songs`` and the
worker side, against Postgres, the fake Spotify, the fake MOWL and ``FakeProvider``: the shared
playlists are the fill's sources, the note reaches the song stages as the instructor's words and
nothing else, the connection and consent refusals write nothing, the answer is fixed once a fill
was started, a row stored before behaves as today. Never the network, a real account or a model."""

import json
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat import connections
from classbeat.classes import song_fill
from classbeat.connections.spotify_consent_policy import CONSENT_VERSION
from classbeat.design.music_decision import NOTE_LABEL
from composertools import SKETCH, ComposerEnv, fill_script, swap, swap_script
from ctx import make_context
from songtools import Rider

pytestmark = pytest.mark.usefixtures("jenv")

A, B, C = "0123456789abcdefABCDEF", "ABCDEF0123456789abcdef", "abcdefABCDEF0123456789"
UNDECIDED: dict[str, Any] = {"state": "undecided"}
PICK: dict[str, Any] = {"state": "decided", "choice": "pick"}
NOTE = "Dark techno, no vocals"


def shared(*ids: str) -> dict[str, Any]:
    return {
        "choice": "playlists",
        "playlists": [{"kind": "spotify_playlist", "id": i} for i in ids],
    }


async def put_music(rider: Rider, class_id: UUID, body: Any) -> httpx.Response:
    browser = rider.browser
    response = await browser.client.put(
        f"/api/classes/{class_id}/music", headers=await browser.headers(), json=body
    )
    browser.client.cookies.clear()
    return response


def fill_jobs(cenv: ComposerEnv, class_id: UUID) -> list[Any]:
    return cenv.sql(
        "SELECT id, input FROM jobs WHERE class_id = :c AND kind = 'design_with_songs'",
        c=class_id,
    ).all()


def decision(cenv: ComposerEnv, class_id: UUID) -> Any:
    return cenv.sql(
        "SELECT interpretation -> 'music_decision' FROM classes WHERE id = :c", c=class_id
    ).scalar()


def disconnect_spotify(cenv: ComposerEnv, rider: Rider) -> None:
    cenv.sql(
        "DELETE FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=rider.user_id
    )


def edited_sketch(cenv: ComposerEnv, rider: Rider, class_id: UUID, sketch: UUID) -> UUID:
    """A newer sketch made current, as a chat edit of the sketch leaves the class."""
    newer = UUID(int=sketch.int + 1)
    cenv.insert_sketch(rider.user_id, class_id, newer, {**SKETCH, "name": "Edited"}, number=2)
    cenv.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=newer, c=class_id)
    return newer


async def test_the_shared_playlists_are_the_fills_sources_on_the_edited_sketch(
    cenv: ComposerEnv,
) -> None:
    """AC 3: answered with two playlists while Spotify is not connected, then connected and the
    working playlist allowed: the fill favours exactly those two, on the current sketch."""
    rider = await cenv.songenv.rider(consent=False)
    disconnect_spotify(cenv, rider)
    class_id, sketch = await cenv.sketched(rider, sources=[C], decision=UNDECIDED)
    answered = await put_music(rider, class_id, shared(A, B))
    assert answered.status_code == 200, answered.text
    assert answered.json() == {"state": "decided", "choice": "playlists", "playlists": 2}
    assert cenv.songenv.counts() == (0, 0, 0)  # no Spotify, MOWL or model call
    current = edited_sketch(cenv, rider, class_id, sketch)
    refused = await cenv.fill(rider, class_id)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_connected")
    ctx = make_context(rider.user_id)
    await cenv.songenv.spot.seed(ctx)
    assert await connections.give_pool_consent(cenv.songenv.factory, ctx, CONSENT_VERSION)
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    [job] = fill_jobs(cenv, class_id)
    assert job.input["base_revision_id"] == str(current)
    assert [s["id"] for s in job.input["sources"]] == [A, B]  # the request's C is not favoured
    assert "music_note" not in job.input
    shown = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert shown["music_decision"] == {"state": "decided", "choice": "playlists", "playlists": 2}


async def test_with_spotify_and_no_consent_a_decided_fill_writes_nothing(
    cenv: ComposerEnv,
) -> None:
    """AC 4: ``consent_missing`` from the create, before any write: no job, no Spotify, MOWL or
    model call."""
    rider = await cenv.songenv.rider(consent=False)
    class_id, _ = await cenv.sketched(rider, decision=PICK)
    before = cenv.songenv.counts()
    refused = await cenv.fill(rider, class_id)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "consent_missing")
    assert fill_jobs(cenv, class_id) == []
    assert cenv.songenv.counts() == before
    assert cenv.songenv.pool(rider.user_id) is None  # no working playlist


@pytest.mark.parametrize("given", [UNDECIDED, PICK])
async def test_without_spotify_the_fill_writes_nothing(
    cenv: ComposerEnv, given: dict[str, Any]
) -> None:
    rider = await cenv.songenv.rider()
    disconnect_spotify(cenv, rider)
    class_id, _ = await cenv.sketched(rider, decision=given)
    before = cenv.songenv.counts()
    refused = await cenv.fill(rider, class_id)
    reason = "music_undecided" if given is UNDECIDED else "not_connected"
    assert (refused.status_code, refused.json()["detail"]) == (409, {"reason": reason})
    assert fill_jobs(cenv, class_id) == []
    assert cenv.songenv.counts() == before
    assert (
        "fill"
        not in cenv.sql("SELECT interpretation FROM classes WHERE id = :c", c=class_id).scalar()
    )


async def test_once_a_fill_was_started_the_answer_is_fixed(cenv: ComposerEnv) -> None:
    """AC 5: whatever the fill's state; a new sketch with no fill takes an answer again."""
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider, decision=PICK)
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    for state in ("queued", "failed"):
        cenv.sql(
            "UPDATE jobs SET state = :s, error_category = CASE WHEN :s = 'failed' "
            "THEN 'upstream_unavailable' END, finished_at = CASE WHEN :s = 'failed' THEN now() "
            "END WHERE id = :j",
            s=state,
            j=UUID(made.json()["job_id"]),
        )
        refused = await put_music(rider, class_id, shared(A))
        assert (refused.status_code, refused.json()["detail"]) == (409, {"reason": "music_fixed"})
        assert decision(cenv, class_id)["choice"] == "pick"
    edited_sketch(cenv, rider, class_id, sketch)
    again = await put_music(rider, class_id, shared(A))
    assert again.status_code == 200, again.text


async def test_a_row_stored_before_behaves_as_today(cenv: ComposerEnv) -> None:
    """AC 7: no ``music_decision``: decided as requested, the request's playlists favoured."""
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, sources=[A, B])
    shown = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert shown["music_decision"] == {"state": "decided", "choice": "request", "playlists": 2}
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    [job] = fill_jobs(cenv, class_id)
    assert [s["id"] for s in job.input["sources"]] == [A, B]
    assert "music_note" not in job.input  # the stored input of today, byte for byte
    assert decision(cenv, class_id) is None  # nothing was written to the old row


async def test_the_note_reaches_the_song_stages_only_as_the_instructors_words(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    answered = await put_music(rider, class_id, {"choice": "describe", "note": f" {NOTE}​\n"})
    assert answered.json() == {"state": "decided", "choice": "describe", "playlists": 0}
    assert NOTE not in answered.text  # never answered back
    assert decision(cenv, class_id)["note"] == NOTE  # cleaned
    earlier = len(cenv.songenv.providers)
    job_id, _, _ = await cenv.waiting(rider, class_id, fill_script())
    assert cenv.songenv.row(job_id).input["music_note"] == NOTE
    sent = [s for p in cenv.songenv.providers[earlier:] for s in p.sent]
    stages = {s.spec.stage: s.spec for s in sent}
    for stage in ("pick_pool", "choose_songs"):
        assert NOTE_LABEL + NOTE in stages[stage].first_message
        assert NOTE not in stages[stage].system
    assert NOTE not in stages["revise"].first_message  # the design keeps the sketch's brief
    brief = cenv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar()
    assert NOTE not in str(brief)  # the class's brief is never changed


async def test_an_answer_changed_while_the_fill_plans_is_the_one_it_takes(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fill's hold compares the decision it planned with the stored one, under the row lock:
    an answer stored between the plan and the create makes the call plan again."""
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=PICK)
    real = song_fill._plan  # pyright: ignore[reportPrivateUsage]
    calls: list[int] = []

    async def answered_meanwhile(*args: Any, **kwargs: Any) -> Any:
        planned = await real(*args, **kwargs)
        if not calls:
            assert (await put_music(rider, class_id, shared(B))).status_code == 200
        calls.append(1)
        return planned

    monkeypatch.setattr(song_fill, "_plan", answered_meanwhile)
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    assert len(calls) == 2
    [job] = fill_jobs(cenv, class_id)
    assert [s["id"] for s in job.input["sources"]] == [B]


@pytest.mark.parametrize(
    "body",
    [
        {"choice": "playlists"},
        shared(*(c * 22 for c in "abcdef")),  # six
        shared(A, A),
        shared("short"),
        {"choice": "describe"},
        {"choice": "describe", "note": "x" * 201},
        {"choice": "later", "note": "techno"},
        {"choice": "request"},
        {"choice": "pick", "extra": 1},
        "pick",
    ],
)
async def test_an_answer_that_does_not_parse_is_422_and_changes_nothing(
    cenv: ComposerEnv, body: Any
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    refused = await put_music(rider, class_id, body)
    assert (refused.status_code, refused.json()["detail"]) == (422, {"reason": "invalid_input"})
    assert decision(cenv, class_id) == UNDECIDED


async def test_an_answer_needs_a_composer_sketch_and_the_composer_on(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    old = await rider.create()  # a class of the old flow: no reading, no sketch
    old_id = UUID(old.json()["class_id"])
    refused = await put_music(rider, old_id, {"choice": "pick"})
    assert (refused.status_code, refused.json()["detail"]) == (409, {"reason": "music_fixed"})
    unknown = await put_music(rider, UUID(int=7), {"choice": "pick"})
    assert (unknown.status_code, unknown.json()["detail"]) == (404, {"reason": "not_found"})
    saved, _ = await cenv.sketched(rider, decision=UNDECIDED)
    draft = uuid.uuid4()  # after phase 2 a draft is current: no sketch to answer for
    cenv.insert_sketch(rider.user_id, saved, draft, {**SKETCH, "name": "Saved"}, number=2)
    cenv.sql(
        "UPDATE class_revisions SET provenance = CAST(:p AS jsonb) WHERE id = :r",
        p=json.dumps({"origin": "design_with_songs"}),
        r=draft,
    )
    cenv.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=draft, c=saved)
    drafted = await put_music(rider, saved, {"choice": "pick"})
    assert (drafted.status_code, drafted.json()["detail"]) == (409, {"reason": "music_fixed"})
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    cenv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")  # the operator's switch off
    off = await put_music(rider, class_id, {"choice": "pick"})
    assert (off.status_code, off.json()["detail"]) == (409, {"reason": "feature_off"})
    assert decision(cenv, class_id) == UNDECIDED


async def test_the_note_is_carried_into_the_swap_and_the_change(cenv: ComposerEnv) -> None:
    """The Architect's decision on #1507: a swap and a change replace the fill with its note, and
    the swap's song search reads it as the fill's does (the change keeps its songs: no search)."""
    rider = await cenv.songenv.rider()
    described = {"state": "decided", "choice": "describe", "note": NOTE}
    class_id, _ = await cenv.sketched(rider, decision=described)
    _, list_id, _ = await cenv.waiting(rider, class_id, fill_script())
    swapped = await swap(rider, class_id, list_id)
    assert swapped.status_code == 201, swapped.text
    swap_id = UUID(swapped.json()["job_id"])
    assert cenv.songenv.row(swap_id).input["music_note"] == NOTE
    earlier = len(cenv.songenv.providers)
    cenv.songenv.script = swap_script()
    row = await cenv.songenv.run(swap_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    pool = [
        s.spec
        for p in cenv.songenv.providers[earlier:]
        for s in p.sent
        if s.spec.stage == "pick_pool"
    ]
    assert pool
    assert all(NOTE_LABEL + NOTE in spec.first_message for spec in pool)
    message = str(uuid.uuid4())
    base = f"/api/classes/{class_id}/pending-messages"
    stored = await rider.browser.post(base, {"message_id": message, "body": "Longer warm-up."})
    assert stored.status_code == 201, stored.text
    changed = await rider.browser.post(f"{base}/{message}/submit", None)
    assert changed.status_code == 201, changed.text
    change = cenv.songenv.row(UUID(changed.json()["job_id"]))
    assert (change.input["mode"], change.input["music_note"]) == ("change", NOTE)
