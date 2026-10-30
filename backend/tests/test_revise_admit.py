"""``revise_draft`` at creation (ADR 0007 sections 3 and 4): the refusals (stale base, a base that
is not a draft of the class, the revision limit, the full chat) create no job and no message;
the limits are the settings'; messages only name a job of their own class; the input model."""

import subprocess
import sys
import uuid
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from classbeat import design
from classbeat.config import Settings
from classbeat.db import user_session
from classbeat.design import chat
from classbeat.design import limits as design_limits
from classbeat.jobs import KINDS
from ctx import make_context
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")


def jobs_of(renv: ReviseEnv, class_id: UUID) -> int:
    return renv.sql(
        "SELECT count(*) FROM jobs WHERE class_id = :c AND kind = 'revise_draft'", c=class_id
    ).scalar_one()


def test_both_processes_register_the_kind_and_it_consumes_no_credit() -> None:
    assert KINDS.get("revise_draft") is design.REVISE_DRAFT
    assert design.REVISE_DRAFT.consumes_credit is False
    assert design.REVISE_DRAFT.admit is not None
    for entry in ("classbeat.web", "classbeat.worker.__main__"):
        code = (
            f"import {entry}; from classbeat.jobs import KINDS; "
            "import sys; sys.exit(0 if KINDS.get('revise_draft') else 1)"
        )
        assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0, entry  # noqa: S603


def test_the_settings_default_to_the_adr() -> None:
    settings = Settings()
    assert (settings.revisions_per_class, settings.chat_max_requests) == (25, 50)
    assert settings.chat_context_requests == 10
    design_limits.configure(settings)
    assert design_limits.current().max_requests == 50
    assert design_limits.current().context_requests == 10
    assert design_limits.current().policy == design_limits.SettingRevisionPolicy(25)
    design_limits.configure(None)


async def test_a_stale_base_is_refused_and_creates_nothing(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    await renv.revise(class_id, base, "revise_good")
    before = (len(renv.messages(class_id)), jobs_of(renv, class_id))
    renv.use("revise_good")
    stale = await renv.ask(class_id, base)  # the browser still shows the first revision
    assert (stale.status_code, stale.json()) == (409, {"detail": {"reason": "stale_base"}})
    assert (len(renv.messages(class_id)), jobs_of(renv, class_id)) == before


async def test_a_base_that_is_not_a_draft_of_the_class_is_invalid_input_alike(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    other_class, other_base = await renv.design()
    renv.sql("UPDATE jobs SET state = 'completed' WHERE class_id = :c", c=other_class)
    answers = [
        await renv.ask(class_id, other_base),  # a revision of another class of the same user
        await renv.ask(class_id, uuid.uuid4()),  # unknown
    ]
    for answer in answers:
        assert (answer.status_code, answer.json()) == (
            409, {"detail": {"reason": "invalid_input"}}
        )  # fmt: skip
    renv.sql("UPDATE class_revisions SET kind = 'song_list' WHERE id = :i", i=base)
    song_list = await renv.ask(class_id, base)
    assert song_list.json() == {"detail": {"reason": "invalid_input"}}
    assert (len(renv.messages(class_id)), jobs_of(renv, class_id)) == (0, 0)


async def test_the_server_fields_cannot_come_from_the_browser(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    for field, value in (
        ("base_revision_hash", "0" * 64),
        ("request_message_id", str(uuid.uuid4())),
    ):
        refused = await renv.ask(class_id, base, **{field: value})
        assert refused.status_code == 422, refused.text
    assert jobs_of(renv, class_id) == 0


async def test_the_request_is_cleaned_and_limited(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    for request in ("", "  " + chr(0x202E) + chr(0) + " ", "x" * 2001):
        refused = await renv.ask(class_id, base, request)
        assert refused.status_code == 422, request
    assert jobs_of(renv, class_id) == 0
    renv.use("revise_good")
    made = await renv.ask(
        class_id, base, "  Harder" + chr(0x202E) + " start" + chr(10) + " please " + "y" * 1960
    )
    assert made.status_code == 201, made.text
    [message] = renv.messages(class_id)
    assert message.body.startswith("Harder start  please yyy")
    assert chr(0x202E) not in message.body
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert row.input["base_revision_hash"] == bytes(renv.revisions(class_id)[0].content_hash).hex()
    assert row.input["request_message_id"] == str(message.id)
    assert row.credit_state == "none"


async def test_the_26th_chat_revision_is_refused_with_its_counts_and_creates_nothing(
    renv: ReviseEnv,
) -> None:
    """The first design is not counted; chat revisions are seeded as copies of the base."""
    class_id, base = await renv.design()

    def seed(count: int, offset: int) -> None:
        renv.sql(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance, parent_id) SELECT gen_random_uuid(), owner_id, "
            "class_id, :offset + n, kind, content, "
            "sha256(convert_to(:offset || '/' || n, 'UTF8')), "
            "music, jsonb_set(provenance, '{origin}', '\"revise_draft\"'), id "
            "FROM class_revisions, generate_series(1, :count) n WHERE id = :b",
            b=base,
            count=count,
            offset=offset,
        )

    seed(24, 1)
    renv.use("revise_good")
    allowed = await renv.ask(class_id, base)  # 24 chat revisions: this is the 25th
    assert allowed.status_code == 201, allowed.text
    renv.sql(
        "UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE class_id = :c", c=class_id
    )
    seed(1, 100)  # the 25th exists
    before = (len(renv.messages(class_id)), jobs_of(renv, class_id))
    refused = await renv.ask(class_id, base)  # the 26th
    assert (refused.status_code, refused.json()) == (
        409, {"detail": {"reason": "revision_limit", "limit": 25, "count": 25}}
    )  # fmt: skip
    assert (len(renv.messages(class_id)), jobs_of(renv, class_id)) == before


async def test_the_51st_request_is_refused_with_its_counts_and_creates_nothing(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    renv.sql(
        "INSERT INTO class_messages (id, owner_id, class_id, seq, role, body) "
        "SELECT gen_random_uuid(), owner_id, id, n, 'user', 'earlier request' "
        "FROM classes, generate_series(1, 50) n WHERE id = :c",
        c=class_id,
    )
    refused = await renv.ask(class_id, base)
    assert (refused.status_code, refused.json()) == (
        409, {"detail": {"reason": "chat_full", "limit": 50, "count": 50}}
    )  # fmt: skip
    assert (len(renv.messages(class_id)), jobs_of(renv, class_id)) == (50, 0)
    renv.sql("DELETE FROM class_messages WHERE class_id = :c AND seq = 50", c=class_id)
    renv.use("revise_good")
    assert (await renv.ask(class_id, base)).status_code == 201  # 49 stored: the 50th is allowed


async def test_the_limits_come_from_the_settings_and_the_policy_seam(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    settings = renv.denv.app.settings
    design_limits.configure(settings.model_copy(update={"revisions_per_class": 1}))
    await renv.revise(class_id, base, "revise_good")
    current = renv.current(class_id)
    assert current is not None
    refused = await renv.ask(class_id, current)
    assert refused.json() == {"detail": {"reason": "revision_limit", "limit": 1, "count": 1}}

    class Policy:
        async def limit_for(self, db: Any, ctx: Any, class_id: UUID) -> int:
            return 2  # a tier's figure (M5)

    design_limits.configure(settings, Policy())
    renv.use("revise_second")
    assert (await renv.ask(class_id, current)).status_code == 201
    renv.sql(
        "UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE class_id = :c", c=class_id
    )
    design_limits.configure(settings.model_copy(update={"chat_max_requests": 2}))
    full = await renv.ask(class_id, current)
    assert full.json() == {"detail": {"reason": "chat_full", "limit": 2, "count": 2}}


async def test_a_message_only_names_a_job_of_its_own_class(renv: ReviseEnv) -> None:
    """``class_messages.job_id`` is checked by the schema for the owner only: the insert must
    refuse a job of another class of the same user, for either role."""
    class_a, _ = await renv.design()
    class_b, _ = await renv.design()
    job_b = renv.sql("SELECT id FROM jobs WHERE class_id = :c", c=class_b).scalar_one()
    ctx = make_context(renv.owner)
    for role in ("user", "assistant"):
        async with user_session(renv.denv.ops.factory, ctx) as db:
            with pytest.raises(chat.MessageRefused):
                await chat.insert_message(
                    db, owner_id=renv.owner, class_id=class_a, job_id=job_b, role=role,  # type: ignore[arg-type]
                    body="x", base_revision_id=None,
                )  # fmt: skip
    assert renv.messages(class_a) == []
    async with user_session(renv.denv.ops.factory, ctx) as db:
        seq = await chat.insert_message(
            db, owner_id=renv.owner, class_id=class_b, job_id=job_b, role="user", body="x",
            base_revision_id=None,
        )  # fmt: skip
    assert seq == 1
    # and a revision's parent must be of the job's own class too
    one = renv.sql("SELECT id FROM class_revisions WHERE class_id = :c", c=class_a).scalar_one()
    assert one is not None
    await renv.ask(class_b, one)  # a base of another class: invalid_input, nothing created
    assert jobs_of(renv, class_b) == 0


def test_the_input_model() -> None:
    values = {
        "class_id": str(uuid.uuid4()),
        "base_revision_id": str(uuid.uuid4()),
        "request": "Longer cooldown",
        "mowl_connection_id": str(uuid.uuid4()),
    }
    ok = design.ReviseDraftInput.model_validate(values)
    assert (ok.base_revision_hash, ok.request_message_id) == (None, None)
    for bad in (
        {**values, "class_id": None, "title": "new"},  # a revision names its class
        {**values, "extra": 1},
        {**values, "base_revision_hash": "XYZ"},
    ):
        with pytest.raises(ValidationError):
            design.ReviseDraftInput.model_validate(bad)
