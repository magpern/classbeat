"""SR C7 (ADR 0036 section 6, ADR 0005 section 7): ``produce_outputs`` for the playlists, on the
fakes (the SwedeBeat gateway, Spotify), against Postgres as ``classbeat_app``; never the network.

- the SwedeBeat playlist alone (the mix and MOWL off): one create with the spec's body, one
  publish of that id, the output ``done``; a second approval writes nothing again (at most once);
- a publish that failed resumes **on the stored ``playlist_id``**, never a second create; a
  publish in doubt is settled by every page of ``/playlists/shared``;
- a create in doubt (a 5xx after the work, a cut connection) blocks every new write to that
  output; Check again keeps it in doubt; "Create another anyway" opens attempt 2 with new keys
  and the old row stays on record; a create that never left (no connection) is retried;
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
