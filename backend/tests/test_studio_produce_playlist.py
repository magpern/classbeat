"""SR C7 (ADR 0036 section 6, ADR 0005 section 7): ``produce_outputs`` for the playlists, on the
fakes (the SwedeBeat gateway, Spotify), against Postgres as ``classbeat_app``; never the network.

- the SwedeBeat playlist alone (the mix and MOWL off): one create with the spec's body, one
  publish of that id, the output ``done``; a second approval writes nothing again (at most once);
- a publish that failed resumes **on the stored ``playlist_id``**, never a second create; a
  publish in doubt is settled by every page of ``/playlists/shared``;
- Check again lists a publish in doubt only with the approved connection and generation: gone,
  reconnected without an account on record (#1117: ``test_studio_publish_reconnect``) or not on
  record, it stays in doubt (``connection_changed``, as the check's reason beside the kept
  category), nothing is read and nothing is published again;
- a create in doubt (a 5xx after the work, a cut connection) blocks every new write to that
  output; Check again keeps it in doubt; "Create another anyway" opens attempt 2 with new keys
  and the old row stays on record; a create that never left (no connection) is retried;
- a write intent left ``pending`` by a lost lease (the reaper requeued the job during the call)
  is in doubt and never sent again, by the requeued run or by another approval;
- ``output_limit``: at most ``ATTEMPTS_PER_CLASS`` attempts of a class and ``STARTS_PER_DAY`` in
  any 24 hours (a rolling window), nothing sent and no attempt opened beyond them;
- the gateway's ``403`` (writes off) is a state the user sees; a reconnect between the approval
  and the job is ``connection_changed`` with nothing written;
- the final Spotify playlist is created private and filled in the mix's order;
- the generic operation views never offer an output's row (its Do it anyway is Create another).
"""

from collections.abc import Callable
from typing import Any

import pytest

from classbeat.studio.output_choice_policy import OUTPUTS
from classbeat.swedebeat_studio import config as studio_config
from classbeat.swedebeat_studio import outputs_rows
from gatewaycore import FakeGateway, Reply, Session
from studiooutputs import (
    act,
    choice,
    choose,
    operations,
    outputs_on,  # noqa: F401 - fixture
    produce,
    rows,
    run_job,
    sent,
    view,
)
from studiotools import StudioEnv
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")
CREATE, SHARED = "/playlists", "/playlists/shared"
Publish = Callable[[str, Any, Session], Reply]


def published(fake: FakeGateway) -> list[str]:
    return [pid for pid, p in fake.playlists.items() if p["public"]]


def on_publish(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeGateway,
    wrap: Callable[[Publish, str, Any, Session], Reply],
) -> None:
    real: Publish = fake._publish  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(fake, "_publish", lambda pid, body, s: wrap(real, pid, body, s))  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]


async def test_the_playlist_alone_is_created_and_published_once(studio: StudioEnv) -> None:
    rider, class_id, list_id, current = await designed(studio)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    (create,) = sent(studio.gateway, "POST", CREATE)
    (publish,) = sent(studio.gateway, "POST", "publish")
    (playlist_id,) = studio.gateway.playlists
    assert publish.path == f"/playlists/{playlist_id}/publish"
    assert publish.body == {"confirm_irreversible": True}
    # The body is the spec's, never model text: category 27, playlist crossfade 0, explicit
    # per-track crossfades with the last 0, the list's SwedeBeat titles.
    spec = studio.songs.revision(current.current_revision_id).music["mix_spec"]
    body = create.body
    assert (body["category_id"], body["crossfade_sec"]) == (27, 0)
    assert [t["reference"] for t in body["tracks"]] == [t["reference"] for t in spec["tracks"]]
    assert [t["crossfade_sec"] for t in body["tracks"]] == [*spec["crossfades"], 0]
    listed = {
        s["swedebeat"]["reference"]: s["swedebeat"]
        for s in studio.songs.revision(list_id).content["songs"]
    }
    assert all(t["title"] == listed[t["reference"]]["title"] for t in body["tracks"])
    assert published(studio.gateway) == [playlist_id]
    (row,) = rows(studio, rider.user_id)
    assert (row.output, row.attempt, row.state) == ("swedebeat_playlist", 1, "done")
    assert row.detail["playlist_id"] == playlist_id
    shown = await view(rider, class_id)
    assert shown["swedebeat_playlist"]["state"] == "done"
    assert playlist_id not in str(shown)  # no SwedeBeat id leaves the server
    # At most once: another approval of the same draft writes nothing.
    again = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert again.state == "completed"
    assert len(sent(studio.gateway, "POST", CREATE)) == 1
    assert len(sent(studio.gateway, "POST", "publish")) == 1
    # Neither the mix nor MOWL was needed: no merge.
    assert not [r for r in studio.gateway.requests if "merge" in r.path]


async def test_a_refused_publish_resumes_on_the_stored_id(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _, current = await designed(studio)
    fake = studio.gateway
    refusing = [True]

    def writes_off(real: Publish, pid: str, body: Any, session: Session) -> Reply:
        fake.writes_enabled = not refusing[0]
        try:
            return real(pid, body, session)
        finally:
            fake.writes_enabled = True

    on_publish(monkeypatch, fake, writes_off)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("failed", "writes_off")
    shown = (await view(rider, class_id))["swedebeat_playlist"]
    assert (shown["state"], shown["category"]) == ("failed", "writes_off")  # the user sees it
    assert shown["note"] == "created_not_public"
    assert shown["actions"] == ["retry"]
    (playlist_id,) = fake.playlists
    assert published(fake) == []
    refusing[0] = False
    again = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (again.state, again.error_category) == ("completed", None)
    assert len(sent(fake, "POST", CREATE)) == 1  # never a second record
    assert [r.path for r in sent(fake, "POST", "publish")] == [
        f"/playlists/{playlist_id}/publish"
    ] * 2
    assert published(fake) == [playlist_id]
    assert [(r.attempt, r.state) for r in rows(studio, rider.user_id)] == [(1, "done")]


async def test_a_publish_in_doubt_is_found_on_a_later_shared_page(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _, current = await designed(studio)
    fake = studio.gateway
    fake.shared_page_size = 1

    def answer_lost(real: Publish, pid: str, body: Any, session: Session) -> Reply:
        for n in range(3):  # three of the user's published playlists listed before ours
            fake.playlists[f"older-{n}"] = {
                "email": session.email, "name": "x", "tracks": [], "public": True,
            }  # fmt: skip
        fake.inject("status", f"/playlists/{pid}/publish", status=502)  # after the work
        return real(pid, body, session)

    on_publish(monkeypatch, fake, answer_lost)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    # In doubt, then settled by every page until the empty one: ours is on the fourth.
    assert [r.query for r in sent(fake, "GET", SHARED)] == [f"page={n}" for n in range(1, 6)]
    assert len(sent(fake, "POST", "publish")) == 1  # never published twice
    (row,) = rows(studio, rider.user_id)
    assert row.state == "done"


async def test_a_publish_that_failed_unseen_is_published_again_on_the_same_id(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _, current = await designed(studio)
    fake = studio.gateway
    calls: list[str] = []

    def fails_once(real: Publish, pid: str, body: Any, session: Session) -> Reply:
        calls.append(pid)
        if len(calls) == 1:  # a 502 with nothing published: in doubt, listed nowhere
            return Reply(502, b'{"detail": "SwedeBeat failed"}')
        return real(pid, body, session)

    on_publish(monkeypatch, fake, fails_once)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    if done.state != "completed":  # reconciled as not published: the next approval resumes
        done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    (playlist_id,) = fake.playlists
    assert calls == [playlist_id, playlist_id]  # the stored id each time
    assert len(sent(fake, "POST", CREATE)) == 1
    assert published(fake) == [playlist_id]


CHANGES = {
    "reconnected": "UPDATE connections SET generation = generation + 1 WHERE id = :c",
    "flipped": "UPDATE connections SET status = 'needs_reconnect' WHERE id = :c",
    "not_on_record": (
        "UPDATE external_operations SET request_context = request_context - 'connection_id' "
        "WHERE kind = 'swedebeat_publish_playlist' "
        "AND owner_id = (SELECT owner_id FROM connections WHERE id = :c)"
    ),
}


@pytest.mark.parametrize("change", [None, *CHANGES])
async def test_check_again_lists_a_publish_only_with_the_approved_connection(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, change: str | None
) -> None:
    rider, class_id, _, current = await designed(studio)
    fake = studio.gateway

    def lost(real: Publish, pid: str, body: Any, session: Session) -> Reply:
        fake.inject("status", SHARED, status=502)  # the job's own check reads nothing
        return Reply(502, b'{"detail": "SwedeBeat failed"}')  # nothing published: in doubt

    on_publish(monkeypatch, fake, lost)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("failed", "uncertain_write")
    assert (await view(rider, class_id))["swedebeat_playlist"]["state"] == "in_doubt"
    if change is not None:
        studio.songs.sql(CHANGES[change], c=rider.swedebeat_connection_id)
    listed_before = len(sent(fake, "GET", SHARED))
    body = {"revision_id": str(current.current_revision_id)}
    checked = await act(rider, class_id, "swedebeat_playlist", "check-again", body)
    assert checked.status_code == 200, checked.text
    (publish,) = [
        o for o in operations(studio, rider.user_id) if o.kind == "swedebeat_publish_playlist"
    ]
    if change is None:  # the approved connection: every page read, not listed, safe to publish
        assert len(sent(fake, "GET", SHARED)) > listed_before
        assert (checked.json()["state"], publish.state) == ("failed", "failed")
        return
    shown = checked.json()
    # #1117: the reason is the check's own; the job's category the user was shown stays.
    assert (shown["state"], shown["check_reason"]) == ("in_doubt", "connection_changed")
    assert shown["category"] == "uncertain_write"
    assert shown["actions"] == ["check_again", "create_another"]
    assert publish.state == "uncertain"  # never "not listed" under another bearer
    assert len(sent(fake, "GET", SHARED)) == listed_before  # nothing read for it
    assert len(sent(fake, "POST", "publish")) == 1
    assert published(fake) == []


@pytest.mark.parametrize("fault", ["status", "drop"])
async def test_a_create_in_doubt_blocks_until_create_another_anyway(
    studio: StudioEnv, fault: str
) -> None:
    rider, class_id, _, current = await designed(studio)
    fake = studio.gateway
    fake.inject(fault, CREATE, status=502)  # after the work: it may exist
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("failed", "uncertain_write")
    assert len(fake.playlists) == 1  # it did take effect
    shown = (await view(rider, class_id))["swedebeat_playlist"]
    assert (shown["state"], shown["note"]) == ("in_doubt", "may_exist")
    assert shown["actions"] == ["check_again", "create_another"]
    # In doubt blocks every new write: another approval sends nothing.
    await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert len(sent(fake, "POST", CREATE)) == 1
    body = {"revision_id": str(current.current_revision_id)}
    checked = await act(rider, class_id, "swedebeat_playlist", "check-again", body)
    assert checked.status_code == 200, checked.text
    assert checked.json()["state"] == "in_doubt"  # a create cannot be listed: still in doubt
    # Create another anyway needs the duplicate risk acknowledged, then opens attempt 2.
    refused = await act(rider, class_id, "swedebeat_playlist", "create-another", body)
    assert refused.status_code == 422
    another = await act(
        rider, class_id, "swedebeat_playlist", "create-another",
        {**body, "acknowledge_duplicate": True},
    )  # fmt: skip
    assert another.status_code == 200, another.text
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    assert len(sent(fake, "POST", CREATE)) == 2
    states = [(r.attempt, r.state) for r in rows(studio, rider.user_id)]
    assert states == [(1, "in_doubt"), (2, "done")]  # the old row stays on record
    keys = [o.idempotency_key for o in operations(studio, rider.user_id)]
    assert len(set(keys)) == len(keys)
    assert any(k.endswith(":swedebeat_playlist:1:create") for k in keys)
    assert any(k.endswith(":swedebeat_playlist:2:create") for k in keys)  # new keys
    # The generic operation views never offer these rows.
    listed = await rider.browser.get("/api/external-operations")
    assert listed.status_code == 200
    assert listed.json()["operations"] == []


async def test_a_create_that_never_left_is_retried(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    studio.gateway.inject("connect_error", CREATE)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    assert len(studio.gateway.playlists) == 1
    assert [(r.attempt, r.state) for r in rows(studio, rider.user_id)] == [(1, "done")]


async def test_an_orphaned_pending_create_is_never_sent_again(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _, current = await designed(studio)
    fake = studio.gateway
    job_id = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    real = fake._create  # pyright: ignore[reportPrivateUsage]

    def lease_lost(body: Any, session: Session) -> Reply:
        answer = real(body, session)  # it took effect
        # What the reaper does to an expired lease: a new token, back to the queue. The run's
        # outcome is then fenced out and its write intent stays ``pending`` with the old token.
        studio.songs.sql(
            "UPDATE jobs SET state = 'queued', lease_version = lease_version + 1, "
            "leased_by = NULL, lease_expires_at = NULL WHERE id = :id",
            id=job_id,
        )
        return answer

    monkeypatch.setattr(fake, "_create", lease_lost)
    done = await run_job(studio, job_id)  # the lost run, then the requeued one
    assert done.state != "queued"
    made = operations(studio, rider.user_id)
    (create,) = [o for o in made if o.kind == "swedebeat_create_playlist"]
    assert create.state in ("pending", "uncertain")  # never settled as not written
    assert len(sent(fake, "POST", CREATE)) == 1  # the requeued run sent nothing
    shown = (await view(rider, class_id))["swedebeat_playlist"]
    assert (shown["state"], shown["actions"]) == ("in_doubt", ["check_again", "create_another"])
    again = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert again.state != "queued"
    assert len(sent(fake, "POST", CREATE)) == 1  # nor another approval
    assert sent(fake, "POST", "publish") == []
    assert [(r.attempt, r.state) for r in rows(studio, rider.user_id)] == [(1, "in_doubt")]


async def test_the_output_limit_per_class(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outputs_rows, "ATTEMPTS_PER_CLASS", 1)
    rider, class_id, _, current = await designed(studio)
    studio.gateway.inject("status", CREATE, status=502)  # attempt 1 in doubt
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("failed", "uncertain_write")
    body = {"revision_id": str(current.current_revision_id), "acknowledge_duplicate": True}
    another = await act(rider, class_id, "swedebeat_playlist", "create-another", body)
    assert another.status_code == 200, another.text
    limited = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (limited.state, limited.error_category) == ("failed", "output_limit")
    assert len(sent(studio.gateway, "POST", CREATE)) == 1  # attempt 2 was never opened
    assert [r.attempt for r in rows(studio, rider.user_id)] == [1]


async def test_the_output_limit_per_rolling_day(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    starts = outputs_rows.STARTS_PER_DAY
    studio.songs.sql(  # the user's other classes opened the day's attempts 23 h 59 min ago
        "INSERT INTO studio_output_starts (owner_id, created_at) SELECT :o, "
        "now() - interval '23 hours 59 minutes' FROM generate_series(1, :n)",
        o=rider.user_id, n=starts,
    )  # fmt: skip
    limited = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (limited.state, limited.error_category) == ("failed", "output_limit")
    assert sent(studio.gateway, "POST", CREATE) == []
    assert rows(studio, rider.user_id) == []
    # One minute later they are out of the window: a rolling day, not a calendar one.
    studio.songs.sql(
        "UPDATE studio_output_starts SET created_at = created_at - interval '2 minutes' "
        "WHERE owner_id = :o",
        o=rider.user_id,
    )
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    assert len(sent(studio.gateway, "POST", CREATE)) == 1


async def test_writes_off_is_a_state_the_user_sees(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    studio.gateway.writes_enabled = False
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("failed", "writes_off")
    shown = (await view(rider, class_id))["swedebeat_playlist"]
    assert (shown["state"], shown["category"], shown["actions"]) == (
        "failed", "writes_off", ["retry"],
    )  # fmt: skip
    assert studio.gateway.playlists == {}
    studio.gateway.writes_enabled = True
    again = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (again.state, again.error_category) == ("completed", None)


async def test_a_reconnect_before_the_job_writes_nothing(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    job_id = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    studio.songs.sql(
        "UPDATE connections SET generation = generation + 1 WHERE id = :c",
        c=rider.swedebeat_connection_id,
    )
    done = await run_job(studio, job_id)
    assert (done.state, done.error_category) == ("failed", "connection_changed")
    assert sent(studio.gateway, "POST", CREATE) == []
    assert rows(studio, rider.user_id) == []


async def test_the_spotify_playlist_is_private_and_in_the_mix_order(studio: StudioEnv) -> None:
    rider, class_id, list_id, current = await designed(studio)
    before = set(studio.songs.fake.playlists)
    done = await produce(studio, rider, class_id, current, "spotify_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    spec = studio.songs.revision(current.current_revision_id).music["mix_spec"]
    songs = {
        s["swedebeat"]["reference"]: s["spotify_id"]
        for s in studio.songs.revision(list_id).content["songs"]
    }
    wanted = [f"spotify:track:{songs[t['reference']]}" for t in spec["tracks"]]
    (made,) = [studio.songs.fake.playlists[p] for p in set(studio.songs.fake.playlists) - before]
    assert made.entries == wanted
    assert (made.public, made.collaborative) == (False, False)
    (row,) = rows(studio, rider.user_id)
    assert (row.output, row.state) == ("spotify_playlist", "done")
    keys = [o.idempotency_key for o in operations(studio, rider.user_id)]
    made_keys = [k for k in keys if k.startswith("out:")]  # the pick's own pool writes aside
    assert [k.rsplit(":", 2)[1:] for k in made_keys] == [
        ["1", "spotify_create"],
        ["1", "spotify_fill"],
    ]
    assert all(k.startswith(f"out:{class_id}:") for k in made_keys)
    again = await produce(studio, rider, class_id, current, "spotify_playlist")
    assert again.state == "completed"
    assert set(studio.songs.fake.playlists) - before == {made.id}  # at most once
    assert studio.gateway.playlists == {}  # no SwedeBeat write for it


async def test_another_users_outputs_are_not_found(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    await produce(studio, rider, class_id, current, "swedebeat_playlist")
    other = await studio.rider()
    shown = await other.browser.get(f"/api/classes/{class_id}/outputs")
    assert (shown.status_code, shown.json()["detail"]) == (404, {"reason": "not_found"})
    body = {"revision_id": str(current.current_revision_id)}
    for action, extra in (("check-again", {}), ("create-another", {"acknowledge_duplicate": True})):
        answer = await act(other, class_id, "swedebeat_playlist", action, {**body, **extra})
        assert (answer.status_code, answer.json()["detail"]) == (404, {"reason": "not_found"})
    assert [(r.attempt, r.state) for r in rows(studio, rider.user_id)] == [(1, "done")]


async def test_the_view_lists_the_outputs_the_page_may_offer(studio: StudioEnv) -> None:
    rider, class_id, _, _ = await designed(studio)
    answer = await rider.browser.get(f"/api/classes/{class_id}/outputs")
    assert answer.json() == {"enabled_outputs": list(OUTPUTS), "outputs": []}
    studio_config.configure(studio_config.StudioConfig(frozenset({"design", "swedebeat_mix"})))
    narrowed = await rider.browser.get(f"/api/classes/{class_id}/outputs")
    assert narrowed.json()["enabled_outputs"] == ["spotify_playlist", "swedebeat_mix"]
