"""A2 of ADR 0016: the external operation ``mowl_attach_swedebeat_mix`` (``classbeat.operations.
swedebeat``), its link, its reconciliation after a crash between the attach and the link, the
indexing wait, the deletion of links and analyses when the MOWL connection changes, and the tenant
rules of ``swedebeat_mix_maps``. Against the in-process fake MOWL (``mowlfake``, over loopback
HTTP); never the network.
"""

import dataclasses
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from authtools import Env, running_env
from classbeat import connections
from classbeat.db import user_session
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from classbeat.operations import swedebeat as swedebeat_ops
from classbeat.operations.kinds import OPERATION_KINDS
from classbeat.operations.swedebeat import KIND, IndexedMix, attach_swedebeat_mix, wait_indexed
from ctx import make_context
from jobtools import new_job
from mowlfake import FakeMowl
from optools import KIND as JOB_KIND
from optools import TOKEN_A, TOKEN_B, OpsEnv

pytestmark = pytest.mark.usefixtures("mowl_api")
SAME_ID = "shared-mix-0001"  # the same id in two accounts (ADR 0016 section 8)


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, swedebeat_enabled=True) as running:
        yield running


class MixInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mix_id: str
    connection_id: UUID
    index: bool = False


class Harness:
    """Runs jobs that attach one mix (and optionally wait for it to be indexed)."""

    def __init__(self, ops: OpsEnv) -> None:
        self.ops = ops
        self.attached: list[int] = []
        self.indexed: list[IndexedMix] = []
        self.poll_seconds = 0.01
        self.timeout_seconds = 180.0
        ops.registry = self.registry  # type: ignore[method-assign]

    def registry(self) -> KindRegistry:
        async def run(job_run: JobRun) -> Complete:
            await job_run.stage("music")
            given = job_run.input
            assert isinstance(given, MixInput)
            playlist = await attach_swedebeat_mix(
                job_run, self.ops.keyring, given.mix_id, connection_id=given.connection_id
            )
            self.attached.append(playlist)
            if given.index:
                self.indexed.append(
                    await wait_indexed(
                        job_run,
                        self.ops.keyring,
                        playlist,
                        connection_id=given.connection_id,
                        poll_seconds=self.poll_seconds,
                        timeout_seconds=self.timeout_seconds,
                    )
                )
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(JOB_KIND, MixInput, ("music",), run))
        return registry

    def job(self, owner: UUID, connection: UUID, mix_id: str, **extra: Any) -> UUID:
        body = {"mix_id": mix_id, "connection_id": str(connection), **extra}
        # One attempt: a retryable failure ends the job instead of queueing it again.
        return new_job(self.ops.admin, owner, input=json.dumps(body), max_attempts=1)

    async def run(self, owner: UUID, connection: UUID, mix_id: str, **extra: Any) -> Any:
        job_id = self.job(owner, connection, mix_id, **extra)
        await self.ops.run_next()
        return self.ops.row(job_id)

    def operations(self, owner: UUID) -> list[Any]:
        return [o for o in self.ops.operations(owner) if o.kind == KIND]

    def links(self, owner: UUID) -> list[tuple[str, int]]:
        return self.ops.links(owner)


@pytest.fixture
def harness(xenv: OpsEnv) -> Harness:
    return Harness(xenv)


async def account(h: Harness, token: str = TOKEN_A) -> tuple[UUID, UUID]:
    owner = await h.ops.user()
    return owner, await h.ops.connect(owner, token)


# --- the attach ---------------------------------------------------------------------------------


def test_the_kind_is_registered_and_belongs_to_no_class() -> None:
    assert OPERATION_KINDS.get(KIND) is swedebeat_ops.ATTACH
    request = swedebeat_ops.attach_request("mix-1")
    assert request.class_id is None
    assert request.idempotency_key == "swedebeat:mix-1"
    with pytest.raises(ValueError, match="no class"):
        dataclasses.replace(request, class_id=uuid.uuid4())


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "https://evil.example/x", "x" * 65, "mix\n1"])
def test_a_mix_id_that_is_not_an_id_is_refused_before_anything(bad: str) -> None:
    from classbeat.jobs import JobFailure

    with pytest.raises(JobFailure) as refused:
        swedebeat_ops.attach_request(bad)
    assert refused.value.category == "invalid_input"


async def test_attach_stores_the_link_and_the_next_job_sends_nothing(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Sunday Climb", token=TOKEN_A)
    first = await harness.run(owner, connection, mix)
    assert first.state == "completed", first.error_category
    [op] = harness.operations(owner)
    assert (op.state, op.class_id) == ("succeeded", None)
    [(source_id, playlist)] = harness.links(owner)
    assert (source_id, str(playlist)) == (mix, op.external_id)
    row = harness.ops.sql(
        "SELECT source FROM mowl_playlist_links WHERE owner_id = :o", o=owner
    ).scalar_one()
    assert row == "swedebeat"
    assert mowl_api.attaches == [mix]
    second = await harness.run(owner, connection, mix)
    assert second.state == "completed"
    assert mowl_api.attaches == [mix]  # a repeat for an existing link is a no-op
    assert harness.attached == [playlist, playlist]
    assert len(harness.operations(owner)) == 1


async def test_a_mix_outside_the_callers_own_listing_is_refused_before_any_attach(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness, TOKEN_A)
    mowl_api.add_mix("Mine", token=TOKEN_A)
    theirs = mowl_api.add_mix("Theirs", token=TOKEN_B)
    for wanted in (theirs, "made-up-id", "e2e-mix-0001"):
        failed = await harness.run(owner, connection, wanted)
        assert (failed.state, failed.error_category) == ("failed", "invalid_input")
    assert mowl_api.attaches == []  # nothing was sent
    assert harness.operations(owner) == []
    assert harness.links(owner) == []


async def test_the_same_mix_id_under_two_accounts_is_two_playlists_and_two_private_links(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    alice, alice_connection = await account(harness, TOKEN_A)
    bob, bob_connection = await account(harness, TOKEN_B)
    mowl_api.add_mix("Alice's", token=TOKEN_A, mix_id=SAME_ID)
    mowl_api.add_mix("Bob's", token=TOKEN_B, mix_id=SAME_ID)
    assert (await harness.run(alice, alice_connection, SAME_ID)).state == "completed"
    assert (await harness.run(bob, bob_connection, SAME_ID)).state == "completed"
    (alice_link,), (bob_link,) = harness.links(alice), harness.links(bob)
    assert alice_link[0] == bob_link[0] == SAME_ID
    assert alice_link[1] != bob_link[1]  # each account's own MOWL playlist
    assert mowl_api.attaches == [SAME_ID, SAME_ID]
    # Row security: each sees only its own link and operation.
    for owner, mine in ((alice, alice_link[1]), (bob, bob_link[1])):
        async with user_session(harness.ops.factory, make_context(owner)) as db:
            seen = (await db.execute(text("SELECT playlist_id FROM mowl_playlist_links"))).all()
            operations = await db.execute(text("SELECT external_id FROM external_operations"))
        assert [r.playlist_id for r in seen] == [mine]
        assert [r.external_id for r in operations] == [str(mine)]


async def test_a_job_naming_another_connection_attaches_nothing(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, _ = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    failed = await harness.run(owner, uuid.uuid4(), mix)
    assert (failed.state, failed.error_category) == ("failed", "not_connected")
    assert mowl_api.attaches == []


# --- failures of each endpoint --------------------------------------------------------------


@pytest.mark.parametrize(
    ("endpoint", "code", "category"),
    [
        ("list", 401, "needs_reconnect"),
        ("list", 429, "rate_limited"),
        ("list", 503, "upstream_unavailable"),
        ("attach", 401, "needs_reconnect"),
        ("attach", 429, "rate_limited"),
        # A 5xx may follow a processed write: uncertain, never re-sent.
        ("attach", 503, "uncertain_write"),
    ],
)
async def test_each_endpoint_failure_ends_the_job_with_its_category_and_no_link(
    harness: Harness, mowl_api: FakeMowl, endpoint: str, code: int, category: str
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.status[f"swedebeat_{endpoint}"] = code
    mowl_api.retry_after = "7"
    failed = await harness.run(owner, connection, mix)
    assert failed.state == "failed"
    assert failed.error_category == category  # a single refused PUT is a proven no-effect
    if endpoint == "list":
        assert mowl_api.attaches == []
    assert harness.links(owner) == []


async def test_a_refused_attach_proves_no_effect_and_a_later_job_attaches(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.status["swedebeat_attach"] = 429
    assert (await harness.run(owner, connection, mix)).state == "failed"
    [op] = harness.operations(owner)
    assert op.state == "failed"  # a single refused request had no effect
    mowl_api.status.clear()
    assert (await harness.run(owner, connection, mix)).state == "completed"
    assert len(harness.links(owner)) == 1


# --- a crash between the attach and the link -------------------------------------------------


async def test_a_lost_attach_answer_is_reconciled_in_the_job_from_the_listing(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.attach_answer = "drop"  # MOWL attaches, then the connection is cut
    done = await harness.run(owner, connection, mix)
    assert done.state == "completed", done.error_category
    [op] = harness.operations(owner)
    assert (op.state, op.resolved_by) == ("succeeded", "worker")
    [(source_id, playlist)] = harness.links(owner)
    assert (source_id, str(playlist)) == (mix, op.external_id)
    assert mowl_api.attaches == [mix]  # the reconciler read; nothing was re-sent


async def test_a_garbled_answer_is_reconciled_the_same_way(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.attach_answer = "garbled"
    assert (await harness.run(owner, connection, mix)).state == "completed"
    assert mowl_api.attaches == [mix]
    assert len(harness.links(owner)) == 1


async def test_a_crash_between_the_attach_and_the_link_is_reconciled_by_the_next_job(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    """The listing cannot show the playlist yet (MOWL lags): the first job ends uncertain and
    sends nothing more; once the listing shows it, the next job adopts it and stores the link."""
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.attach_answer = "drop"
    mowl_api.list_shows_playlist = False
    lost = await harness.run(owner, connection, mix)
    assert (lost.state, lost.error_category) == ("failed", "uncertain_write")
    [op] = harness.operations(owner)
    assert (op.state, op.external_id) == ("uncertain", None)
    assert harness.links(owner) == []
    mowl_api.list_shows_playlist = True
    again = await harness.run(owner, connection, mix)
    assert again.state == "completed", again.error_category
    [op] = harness.operations(owner)
    assert (op.state, op.resolved_by) == ("succeeded", "worker")
    [(source_id, playlist)] = harness.links(owner)
    assert (source_id, str(playlist)) == (mix, op.external_id)
    assert mowl_api.attaches == [mix]  # never sent again


async def test_a_listing_that_cannot_show_the_playlist_leaves_the_row_uncertain(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    """The real listing may not carry ``PlaylistID`` (unverified until the live check): then
    nothing is concluded, nothing is re-sent, and the user decides."""
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.attach_answer = "drop"
    mowl_api.list_shows_playlist = False
    for _ in range(3):
        again = await harness.run(owner, connection, mix)
        assert (again.state, again.error_category) == ("failed", "uncertain_write")
    [op] = harness.operations(owner)
    assert op.state == "uncertain"  # never ``failed``: a missing entry proves nothing
    assert harness.links(owner) == []
    assert mowl_api.attaches == [mix]


async def test_a_failing_listing_leaves_the_row_uncertain(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.attach_answer = "drop"
    mowl_api.list_ok_calls = 1  # the own-listing check passes; the reconciler's read fails
    lost = await harness.run(owner, connection, mix)
    assert (lost.state, lost.error_category) == ("failed", "uncertain_write")
    [op] = harness.operations(owner)
    assert op.state == "uncertain"
    assert mowl_api.attaches == [mix]


async def test_the_reconciler_ignores_an_entry_of_another_mix(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    other = mowl_api.add_mix("Other", token=TOKEN_A)
    mowl_api.attach_answer = "drop"
    await harness.run(owner, connection, other)  # only "Other" is attached (and shown)
    mowl_api.attach_answer = "ok"
    assert (await harness.run(owner, connection, other)).state == "completed"
    mowl_api.attach_answer = "drop"
    await harness.run(owner, connection, mix)
    # The row for ``mix`` is uncertain and its entry carries a playlist id of its own.
    assert (await harness.run(owner, connection, mix)).state == "completed"
    links = dict(harness.links(owner))
    assert set(links) == {mix, other}
    assert links[mix] != links[other]


# --- waiting for MOWL to index the mix -----------------------------------------------------------


async def test_the_wait_polls_until_the_mix_is_indexed(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A, listed_seconds=200, index_polls=3)
    done = await harness.run(owner, connection, mix, index=True)
    assert done.state == "completed", done.error_category
    [indexed] = harness.indexed
    assert indexed.duration_seconds == pytest.approx(200.0)
    assert sum(1 for r in mowl_api.requests if r.endswith("/tracks")) == 4


async def test_an_indexing_error_ends_the_job_retryably_and_keeps_the_link(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A, index_error="unsupported")
    failed = await harness.run(owner, connection, mix, index=True)
    assert failed.error_category == "upstream_unavailable"
    assert len(harness.links(owner)) == 1  # the attach happened and stays


async def test_an_index_that_never_finishes_times_out(harness: Harness, mowl_api: FakeMowl) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A, index_polls=10_000)
    harness.timeout_seconds = 0.05
    failed = await harness.run(owner, connection, mix, index=True)
    assert failed.error_category == "upstream_unavailable"
    assert sum(1 for r in mowl_api.requests if r.endswith("/tracks")) < 50


@pytest.mark.parametrize(
    ("code", "category"), [(401, "needs_reconnect"), (503, "upstream_unavailable")]
)
async def test_a_tracks_failure_ends_the_wait_with_its_category(
    harness: Harness, mowl_api: FakeMowl, code: int, category: str
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.status["swedebeat_tracks"] = code
    failed = await harness.run(owner, connection, mix, index=True)
    assert failed.error_category == category


# --- the MOWL connection changes ---------------------------------------------------------------


def plant_map(h: Harness, owner: UUID, connection: UUID, mix_id: str = "mix-1") -> None:
    h.ops.sql(
        "INSERT INTO swedebeat_mix_maps (owner_id, mix_id, mowl_connection_id, playlist_id, name, "
        "duration, listed_duration, map) VALUES (:o, :m, :c, 9001, 'Planted name', 600, 600, "
        "CAST(:map AS jsonb))",
        o=owner,
        m=mix_id,
        c=connection,
        map=json.dumps({"songs": []}),
    )


COUNTS = (
    "SELECT count(*) FROM mowl_playlist_links WHERE owner_id = :o",
    "SELECT count(*) FROM swedebeat_mix_maps WHERE owner_id = :o",
)


def stored(h: Harness, owner: UUID) -> tuple[int, int, int]:
    links, maps = (h.ops.sql(statement, o=owner).scalar_one() for statement in COUNTS)
    return links, maps, len(h.operations(owner))


async def attached_with_map(h: Harness, mowl_api: FakeMowl) -> tuple[UUID, UUID]:
    owner, connection = await account(h)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    assert (await h.run(owner, connection, mix)).state == "completed"
    plant_map(h, owner, connection, mix)
    assert stored(h, owner) == (1, 1, 1)
    return owner, connection


async def test_a_reconnect_as_another_mowl_account_deletes_links_operations_and_maps(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, _ = await attached_with_map(harness, mowl_api)
    other = await account(harness, TOKEN_B)  # another user, untouched
    await harness.ops.connect(owner, TOKEN_B)  # the same user, now another MOWL account
    assert stored(harness, owner) == (0, 0, 0)
    assert other[0] != owner


async def test_disconnect_mowl_deletes_links_operations_and_maps(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, _ = await attached_with_map(harness, mowl_api)
    other, other_connection = await account(harness, TOKEN_B)
    other_mix = mowl_api.add_mix("Bob's", token=TOKEN_B)
    assert (await harness.run(other, other_connection, other_mix)).state == "completed"
    plant_map(harness, other, other_connection, other_mix)
    assert await connections.disconnect(harness.ops.factory, make_context(owner), "mowl")
    assert stored(harness, owner) == (0, 0, 0)
    assert stored(harness, other) == (1, 1, 1)  # nobody else's


async def test_deleting_the_account_deletes_the_maps(harness: Harness, mowl_api: FakeMowl) -> None:
    owner, _ = await attached_with_map(harness, mowl_api)
    harness.ops.sql("DELETE FROM users WHERE id = :o", o=owner)
    assert stored(harness, owner) == (0, 0, 0)


# --- the table ----------------------------------------------------------------------------------


async def test_the_table_is_private_to_its_owner_and_unique_per_owner_and_mix(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    alice, alice_connection = await account(harness, TOKEN_A)
    bob, bob_connection = await account(harness, TOKEN_B)
    plant_map(harness, alice, alice_connection, SAME_ID)
    plant_map(harness, bob, bob_connection, SAME_ID)  # the same id under another owner is fine
    with pytest.raises(DBAPIError):
        plant_map(harness, alice, alice_connection, SAME_ID)  # not twice for one owner
    for owner in (alice, bob):
        async with user_session(harness.ops.factory, make_context(owner)) as db:
            rows = (await db.execute(text("SELECT owner_id FROM swedebeat_mix_maps"))).all()
            changed = await db.execute(text("UPDATE swedebeat_mix_maps SET name = 'x'"))
            removed = await db.execute(text("DELETE FROM swedebeat_mix_maps"))
        assert [r.owner_id for r in rows] == [owner]
        counts = (cast(Any, changed).rowcount, cast(Any, removed).rowcount)
        assert counts == (1, 1)  # only its own row
    assert stored(harness, alice)[1] == stored(harness, bob)[1] == 0


@pytest.mark.parametrize(
    "column_values",
    [
        {"mix_id": "has space"},
        {"mix_id": "x" * 65},
        {"playlist_id": 0},
        {"duration": 0},
        {"duration": 7201},
        {"listed_duration": -1},
        {"name": "n" * 201},
    ],
)
async def test_the_checks_bound_a_row(harness: Harness, column_values: dict[str, Any]) -> None:
    owner, connection = await account(harness)
    values: dict[str, Any] = {
        "mix_id": "mix-1",
        "playlist_id": 9001,
        "duration": 600,
        "listed_duration": 600,
        "name": "Mix",
    }
    values.update(column_values)
    with pytest.raises(DBAPIError):
        harness.ops.sql(
            "INSERT INTO swedebeat_mix_maps (owner_id, mix_id, mowl_connection_id, playlist_id, "
            "name, duration, listed_duration, map) VALUES (:o, :mix_id, :c, :playlist_id, :name, "
            ":duration, :listed_duration, '{}'::jsonb)",
            o=owner,
            c=connection,
            **values,
        )


async def test_the_export_shows_a_count_and_nothing_of_the_analysis(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    from classbeat.account.export import build_export

    owner, _ = await attached_with_map(harness, mowl_api)
    document = await build_export(harness.ops.factory, make_context(owner))
    assert document is not None
    assert document["swedebeat_mix_analyses"] == 1
    assert "Planted name" not in json.dumps(document)
    assert "swedebeat_mix_maps" not in document
