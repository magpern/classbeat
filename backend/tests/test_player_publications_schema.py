"""Migration 0052 (ADR 0034 section 5, amending ADR 0005 section 1; #1060): the checks it adds.

- ``jobs_class_id``: ``jobs.class_id`` may be NULL for ``player_remove`` only. Every other kind,
  registered or not, is refused without a class.
- ``external_operations_class``: a ``player_send`` carries its class, a ``player_remove`` none.
- ``player_publications``: the counter's bounds, and the sent revision and its 32-byte document
  hash set together or not at all.

As the superuser (the checks hold for every role). Never the network.
"""

import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from jobtools import digest, engine_for, new_class, new_job, new_user

NUMBER_LIMIT = 2**62
JOB = (
    "INSERT INTO jobs (id, owner_id, kind, state, class_id, input, idempotency_key, request_hash, "
    "credit_state) VALUES (:id, :o, :k, 'queued', :c, '{}'::jsonb, :key, :h, 'none') RETURNING id"
)
OPERATION = (
    "INSERT INTO external_operations (owner_id, job_id, class_id, kind, idempotency_key, "
    "request_hash, state, lease_version) VALUES (:o, :j, :c, :k, :key, :h, 'pending', 1)"
)


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def insert_job(admin: Connection, owner: uuid.UUID, kind: str, class_id: uuid.UUID | None) -> Any:
    return admin.execute(
        text(JOB),
        {
            "id": uuid.uuid4(), "o": owner, "k": kind, "c": class_id,
            "key": secrets.token_urlsafe(18), "h": digest(kind),
        },
    ).scalar_one()  # fmt: skip


def refused(admin: Connection, constraint: str, statement: str, **params: Any) -> None:
    with pytest.raises(IntegrityError) as raised:
        admin.execute(text(statement), params)
    assert constraint in str(raised.value)


@pytest.mark.parametrize(
    "kind", ["send_to_player", "design_draft", "save_workout", "test_kind", "player_removed"]
)
def test_every_other_kind_needs_its_class(admin: Connection, kind: str) -> None:
    owner = new_user(admin)
    with pytest.raises(IntegrityError) as raised:
        insert_job(admin, owner, kind, None)
    assert "jobs_class_id" in str(raised.value)


def test_a_removal_is_the_one_kind_without_a_class(admin: Connection) -> None:
    owner = new_user(admin)
    job_id = insert_job(admin, owner, "player_remove", None)
    found = admin.execute(text("SELECT kind, class_id FROM jobs WHERE id = :id"), {"id": job_id})
    assert tuple(found.one()) == ("player_remove", None)
    # The class is not what keeps a removal: its class went before it was made.
    assert insert_job(admin, owner, "send_to_player", new_class(admin, owner)) is not None


def test_a_send_operation_has_its_class_and_a_removal_none(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    send_job = new_job(admin, owner, class_id, kind="send_to_player", credit_state="none")
    remove_job = insert_job(admin, owner, "player_remove", None)
    rows = [
        (False, send_job, None, "player_send"),
        (False, remove_job, class_id, "player_remove"),
        (True, send_job, class_id, "player_send"),
        (True, remove_job, None, "player_remove"),
    ]
    for index, (allowed, job_id, its_class, kind) in enumerate(rows):
        params = {"o": owner, "h": digest("op"), "j": job_id, "c": its_class, "k": kind,
                  "key": f"player:{index}:1"}  # fmt: skip
        if allowed:
            admin.execute(text(OPERATION), params)
        else:
            refused(admin, "external_operations_class", OPERATION, **params)


PUBLICATION = (
    "INSERT INTO player_publications (owner_id, source_class_id, last_number, sent_revision_id, "
    "sent_document_hash) VALUES (:o, :c, :n, :r, :h)"
)


@pytest.mark.parametrize(
    ("number", "revision", "hashed", "constraint"),
    [
        (-1, None, None, "player_publications_last_number"),
        (NUMBER_LIMIT, None, None, "player_publications_last_number"),
        (1, "set", None, "player_publications_sent"),
        (1, None, bytes(32), "player_publications_sent"),
        (1, "set", bytes(31), "player_publications_sent_document_hash"),
    ],
)
def test_the_publication_row_checks(
    admin: Connection, number: int, revision: str | None, hashed: bytes | None, constraint: str
) -> None:
    owner = new_user(admin)
    params = {
        "o": owner, "c": uuid.uuid4(), "n": number,
        "r": uuid.uuid4() if revision else None, "h": hashed,
    }  # fmt: skip
    refused(admin, constraint, PUBLICATION, **params)


def test_a_publication_row_at_the_bounds_is_accepted(admin: Connection) -> None:
    owner = new_user(admin)
    for number, revision, hashed in ((0, None, None), (NUMBER_LIMIT - 1, uuid.uuid4(), bytes(32))):
        admin.execute(
            text(PUBLICATION),
            {"o": owner, "c": uuid.uuid4(), "n": number, "r": revision, "h": hashed},
        )
