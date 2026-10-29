"""The ``design_draft`` pipeline (ADR 0006 section 3, D1) through the job runner, against Postgres
as ``classbeat_app``, the fake MOWL of ``designtools`` (behind the worker's allowlist opener) and
``FakeProvider``. Never the network, never a real account or model.

Covered: a draft on a new playlist (attach, analysis, design stage, revision with music and
provenance); the next job reusing the link and analysing again; the connection checks before
and after the music (#117 item 1); a model failure; one provider per job; the runtime and the
provider factory missing; no token or id in events, runs, logs or model input.
"""

import hashlib
import json
import logging
import uuid
from typing import Any

import pytest
from mowl import music, spotify, workout

from classbeat import design
from classbeat.agent import Provenance, WorkoutDraft
from classbeat.design import Runtime
from classbeat.design import kind as design_kind
from classbeat.design import music as design_music
from designtools import PLAYLIST_NAME, DesignEnv, FakeMowlMusic
from optools import OTHER_PLAYLIST, PLAYLIST, TOKEN_A, TOKEN_B

pytestmark = pytest.mark.usefixtures("jenv")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


async def owner_with_mowl(denv: DesignEnv, token: str = TOKEN_A) -> tuple[uuid.UUID, uuid.UUID]:
    owner = await denv.ops.user()
    return owner, await denv.ops.connect(owner, token)


async def test_a_draft_is_designed_on_a_new_playlist_and_stored(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    owner, conn = await owner_with_mowl(denv)
    job_id = denv.job(owner, conn)
    await denv.run_next()

    row = denv.ops.row(job_id)
    assert (row.state, row.error_category, row.stage) == ("completed", None, "design")
    assert fake_mowl.attaches == [(TOKEN_A, PLAYLIST)]
    assert set(fake_mowl.tokens_read) == {TOKEN_A}
    assert fake_mowl.reads[0] == "/v1/Spotify/Playlists"  # the name
    assert fake_mowl.track_reads == 1
    assert sum("/AudioAnalysis" in r for r in fake_mowl.reads) == len(fake_mowl.music.tracks)

    [revision] = denv.revisions(job_id)
    assert (revision.kind, revision.number, revision.owner_id) == ("draft", 1, owner)
    draft = WorkoutDraft.model_validate_json(json.dumps(revision.content))
    workout.validate(draft.to_spec())
    assert "beat" not in json.dumps(revision.content)  # fitted cadences only
    assert bytes(revision.content_hash) == hashlib.sha256(canonical(revision.content)).digest()

    [(link_id, link_playlist)] = denv.ops.sql(
        "SELECT id, playlist_id FROM mowl_playlist_links WHERE owner_id = :o", o=owner
    ).all()
    assert revision.music == {
        "source": "mowl_spotify_playlist",
        "link_id": str(link_id),
        "fingerprint": revision.music["fingerprint"],
        "unknown_bpm": [],
    }
    assert len(revision.music["fingerprint"]) == 64

    [run] = denv.agent_runs(job_id)
    made = Provenance.model_validate_json(json.dumps(revision.provenance))
    assert (made.provider, made.model, made.model_answered, made.effort) == (
        "fake",
        "fake-model",
        "fake",
        "medium",
    )
    assert made.prompt_version == run.prompt_version
    assert (made.map_version, made.crossfade_sec) == (music.MAP_VERSION, spotify.CROSSFADE_SEC)
    assert made.agent_run_ids == [run.id]
    assert made.cost_micros == run.cost_micros
    assert run.outcome == "submitted"

    events = denv.ops.events(job_id)
    kinds = [k for _, k, _, _ in events]
    assert kinds[:2] == ["claimed", "stage_started"]
    assert [p["stage"] for _, k, p, _ in events if k == "stage_started"] == ["music", "design"]
    assert [p["state"] for _, k, p, _ in events if k == "external_operation"] == [
        "pending",
        "succeeded",
    ]
    assert kinds[-2:] == ["revision_created", "completed"]
    assert events[-1][2] == {"revision_id": str(revision.id)}

    [provider] = denv.providers
    sent = json.dumps([repr(s) for s in provider.sent], default=str)
    assert PLAYLIST_NAME in sent  # the model sees the name, never an id or a token
    stored = json.dumps([p for _, _, p, _ in events]) + json.dumps(
        [dict(r._mapping) for r in denv.agent_runs(job_id)], default=str
    )
    revision_text = json.dumps([revision.content, revision.music, revision.provenance])
    for value in (TOKEN_A, PLAYLIST, str(conn), str(owner), str(link_playlist) + ","):
        assert value not in sent
    for value in (TOKEN_A, PLAYLIST, str(conn), PLAYLIST_NAME, "Invented Song"):
        assert value not in stored
        assert value not in caplog.text
    assert TOKEN_A not in revision_text
    assert PLAYLIST not in revision_text


async def test_the_next_job_reuses_the_link_and_analyses_again(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    owner, conn = await owner_with_mowl(denv)
    first = denv.job(owner, conn)
    await denv.run_next()
    second = denv.job(owner, conn)
    await denv.run_next()
    assert [denv.ops.row(j).state for j in (first, second)] == ["completed", "completed"]
    assert len(fake_mowl.attaches) == 1  # the stored link: no second write
    assert fake_mowl.track_reads == 2  # M3a keeps no analysis: every job reads the music
    [a], [b] = denv.revisions(first), denv.revisions(second)
    assert a.music["link_id"] == b.music["link_id"]
    assert a.music["fingerprint"] == b.music["fingerprint"]
    assert [[s.spec.stage for s in p.sent] for p in denv.providers] == [["design"], ["design"]]
    assert denv.providers[0] is not denv.providers[1]  # one provider per job (#114 item 4)


async def test_a_job_whose_connection_is_not_the_users_calls_nothing(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    owner, _ = await owner_with_mowl(denv)
    job_id = denv.job(owner, uuid.uuid4())
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert fake_mowl.requests == []
    assert denv.providers[0].sent == []


async def test_a_reconnect_after_the_link_was_read_stops_before_any_read(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#117 item 1: the link (account A's playlist) is used only under the connection's lock."""
    owner, conn = await owner_with_mowl(denv)
    real_open = design_music.open_connection

    async def open_then_reconnect(*args: Any, **kwargs: Any) -> Any:
        opened = await real_open(*args, **kwargs)
        # A reconnect in place as another MOWL account, right after the read.
        await denv.ops.connect(owner, TOKEN_B)
        return opened

    monkeypatch.setattr(design_music, "open_connection", open_then_reconnect)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert fake_mowl.attaches == [(TOKEN_A, PLAYLIST)]
    assert fake_mowl.reads == []  # no read of account A's playlist with B's token, or at all
    assert denv.ops.links(owner) == []


async def test_a_reconnect_during_the_design_stores_nothing(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, conn = await owner_with_mowl(denv)
    real_run_stage = design_kind.run_stage

    async def reconnect_then_run(*args: Any, **kwargs: Any) -> Any:
        await denv.ops.connect(owner, TOKEN_B)
        return await real_run_stage(*args, **kwargs)

    monkeypatch.setattr(design_kind, "run_stage", reconnect_then_run)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert denv.revisions(job_id) == []
    [run] = denv.agent_runs(job_id)
    assert run.outcome == "submitted"  # the model ran; its draft is not stored


async def test_a_model_refusal_fails_the_job_and_stores_no_revision(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    denv.script = "refusal"
    owner, conn = await owner_with_mowl(denv)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_refused")
    assert denv.revisions(job_id) == []
    assert [r.outcome for r in denv.agent_runs(job_id)] == ["model_refused"]


async def test_without_a_runtime_the_job_fails_before_any_call(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    design.configure(None)
    owner, conn = await owner_with_mowl(denv)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    assert (denv.ops.row(job_id).state, denv.ops.row(job_id).error_category) == (
        "failed",
        "internal",
    )
    assert fake_mowl.requests == []


async def test_a_provider_that_cannot_be_built_is_model_config_before_any_call(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    def unavailable() -> Any:
        raise RuntimeError("no provider")

    design.configure(
        Runtime(keyring=denv.ops.keyring, provider=unavailable, agent=denv.runtime().agent)
    )
    owner, conn = await owner_with_mowl(denv)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert fake_mowl.requests == []


async def test_an_empty_playlist_is_invalid_input(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    fake_mowl.music.tracks.clear()
    owner, conn = await owner_with_mowl(denv)
    job_id = denv.job(owner, conn, OTHER_PLAYLIST)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert denv.providers[0].sent == []


async def test_the_kind_reads_the_user_only_from_its_job(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    """Two users, two jobs: each run reads with its own owner's token and stores to its own
    class; nothing crosses."""
    owner_a, conn_a = await owner_with_mowl(denv, TOKEN_A)
    owner_b, conn_b = await owner_with_mowl(denv, TOKEN_B)
    job_a = denv.job(owner_a, conn_a)
    job_b = denv.job(owner_b, conn_b)
    await denv.run_next()
    await denv.run_next()
    assert sorted(fake_mowl.attaches) == sorted([(TOKEN_A, PLAYLIST), (TOKEN_B, PLAYLIST)])
    for owner, job_id in ((owner_a, job_a), (owner_b, job_b)):
        [revision] = denv.revisions(job_id)
        assert revision.owner_id == owner
        [link] = denv.ops.sql(
            "SELECT id FROM mowl_playlist_links WHERE owner_id = :o", o=owner
        ).all()
        assert revision.music["link_id"] == str(link.id)
