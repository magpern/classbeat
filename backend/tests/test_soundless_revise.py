"""A soundless class revised by chat (ADR 0007 amendment of 2026-11-03, items 1 to 7; SL2, #528),
against Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider`` scripts. Never the
network, never a real account or model.

Covered: the revision is a child of the base, current by compare-and-set, with its two messages,
music ``{"source": "none"}``, ``origin = revise_draft``, no credit, no MOWL request and no
``music`` stage; the model sees the soundless revise prompt, two tools (no ``songs_requested``),
no ``music`` key and no music value (planted); a "change the songs" request cannot end
``use_change_songs``; earlier requests travel and notes never do; the gate at ``admit`` (the
switch and the routes, nothing written) and the worker's backstops (``feature_off``,
``model_config``) before any call; the music kind never changing in the final store.
"""

import json
from typing import Any
from uuid import UUID

import pytest

from agentfake import Planted
from classbeat import design
from classbeat.agent.records import Provenance
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.soundless import REVISE_SYSTEM_PROMPT
from classbeat.agent.stage import prompt_version
from classbeat.design import revise_soundless as revise_mod  # the soundless run (#904 split)
from classbeat.models import routes as routes_module
from classbeat.models.versions import all_prompt_versions, running_suite_version
from designtools import FakeMowlMusic
from optools import PLAYLIST, TOKEN_A
from revisetools import ReviseEnv
from test_soundless_jobs import (  # noqa: F401  (soundless_on is the autouse fixture)
    MUSIC_WORDS,
    NONE,
    SPOTIFY,
    connections_flag,
    counts,
    gate,
    own_key,
    plan_keys,
    route,
    soundless_class,
    soundless_on,
)

pytestmark = pytest.mark.usefixtures("jenv")

NOTE = "Made the first minutes easier."


async def ask(renv: ReviseEnv, class_id: UUID, base: UUID, request: str = "Make it easier.") -> Any:
    return await renv.ask(class_id, base, request)


def state(renv: ReviseEnv, class_id: UUID) -> tuple[int, int, int, UUID | None]:
    """(revisions, messages, jobs, current): what a refused revise must not change."""
    return (
        len(renv.revisions(class_id)),
        len(renv.messages(class_id)),
        counts(renv)[1],
        renv.current(class_id),
    )


async def refused_with(renv: ReviseEnv, class_id: UUID, base: UUID, reason: str) -> None:
    before = state(renv, class_id)
    refused = await ask(renv, class_id, base)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == reason
    assert state(renv, class_id) == before  # no job, no message, no revision


def sent_text(provider: Any) -> str:
    parts: list[str] = []
    for sent in provider.sent:
        spec = sent.spec
        parts += [spec.system, spec.first_message]
        parts += [json.dumps([t.name, t.description, dict(t.input_schema)]) for t in spec.tools]
        for results, reminder in sent.turns:
            parts += [r.content for r in results] + [reminder or ""]
    return "\n".join(parts)


# --- a soundless revise end to end -----------------------------------------------------------


async def test_a_soundless_class_is_revised_with_no_music_call_and_no_credit(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    credits = counts(renv)[2]
    job_id, _ = await renv.revise(class_id, base.id, "soundless_revise_good", "Make it easier.")
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category, row.credit_state) == ("completed", None, "none")
    assert counts(renv)[2] == credits  # a revise consumes no credit
    assert fake_mowl.requests == []  # no attach, no listing, no analysis: nothing at MOWL
    stages = renv.sql(
        "SELECT payload ->> 'stage' FROM job_events WHERE job_id = :j AND kind = 'stage_started' "
        "ORDER BY seq",
        j=job_id,
    ).scalars()
    assert list(stages) == ["revise"]  # no music stage
    first, second = renv.revisions(class_id)
    assert (second.number, second.kind, second.parent_id, second.job_id) == (
        2, "draft", base.id, job_id,
    )  # fmt: skip
    assert second.music == NONE
    assert first.content != second.content
    WorkoutDraft.model_validate(second.content)  # Save and the page read it unchanged
    made = Provenance.model_validate_json(json.dumps(second.provenance))
    assert (made.origin, made.map_version, made.crossfade_sec) == ("revise_draft", None, None)
    assert made.prompt_version == all_prompt_versions()["soundless_revise"]
    assert renv.current(class_id) == second.id
    stored = renv.sql("SELECT music_source FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert stored == NONE  # the kind never changes
    user, assistant = renv.messages(class_id)
    assert (user.role, user.body, user.job_id, user.base_revision_id) == (
        "user", "Make it easier.", job_id, base.id,
    )  # fmt: skip
    assert (assistant.role, assistant.body, assistant.result_revision_id) == (
        "assistant", NOTE, second.id,
    )  # fmt: skip
    assert assistant.change is not None
    # What the model was sent.
    provider = renv.providers[-1]
    [sent] = provider.sent
    assert sent.spec.system == REVISE_SYSTEM_PROMPT
    assert [t.name for t in sent.spec.tools] == ["preview_draft", "submit_draft"]
    assert prompt_version(sent.spec.system, sent.spec.tools) == made.prompt_version
    payload = json.loads(sent.spec.first_message.split("\n\n", 1)[1])
    assert set(payload) == {"brief", "base_draft", "earlier_requests", "request"}
    assert payload["request"] == "Make it easier."


async def test_earlier_requests_travel_and_a_note_never_does(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    await renv.revise(class_id, base.id, "soundless_revise_good", "Make the start easier")
    second = renv.current(class_id)
    assert second is not None
    renv.use("soundless_revise_second")
    asked = await ask(renv, class_id, second, "Now the second step")
    assert asked.status_code == 201, asked.text
    await renv.run(design.REVISE_KIND)
    provider = renv.providers[-1]
    payload = json.loads(provider.sent[0].spec.first_message.split("\n\n", 1)[1])
    assert payload["earlier_requests"] == ["Make the start easier"]
    assert payload["request"] == "Now the second step"
    assert NOTE not in sent_text(provider)  # the first revision's note is never read back
    assert len(renv.revisions(class_id)) == 3


async def test_no_music_value_reaches_the_model_in_a_soundless_revise(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    """Planted values in every place a music class would read music from: the base revision's
    music, a link row and the account. None reaches the model, and no music word either."""
    planted = Planted()
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    renv.sql(
        "UPDATE class_revisions SET music = CAST(:m AS jsonb) WHERE id = :r",
        m=json.dumps(
            {
                "source": "none",
                "fingerprint": "PlantedFingerprint0042",
                "link_id": planted.connection_id,
                "unknown_bpm": [{"index": 3, "title": planted.unavailable}],
            }
        ),
        r=base.id,
    )
    email = renv.sql("SELECT email FROM users WHERE id = :u", u=renv.owner).scalar_one()
    job_id, _ = await renv.revise(class_id, base.id, "soundless_revise_good", "Make it easier")
    assert renv.denv.ops.row(job_id).state == "completed"
    provider = renv.providers[-1]
    told = sent_text(provider)
    forbidden = [
        *planted.values(), "PlantedFingerprint0042", TOKEN_A, PLAYLIST, email, str(renv.owner),
        str(renv.browser.connection_id), str(class_id), str(base.id), str(job_id),
    ]  # fmt: skip
    for value in forbidden:
        assert value not in told, value
    body = provider.sent[0].spec.first_message
    assert '"music"' not in body
    for word in MUSIC_WORDS:
        assert word not in (told.replace("racetrack", "")).lower().split(), word


async def test_a_change_the_songs_request_cannot_end_use_change_songs(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    job_id, _ = await renv.revise(
        class_id, base.id, "soundless_revise_songs_requested", "Use other songs."
    )
    row = renv.denv.ops.row(job_id)
    # The model tried the cycling exit: the stage has no such tool, so it ends with a revision.
    assert (row.state, row.error_category) == ("completed", None)
    [run] = renv.denv.agent_runs(job_id)
    assert run.stage == "revise"
    assert fake_mowl.requests == []


# --- the gate at creation --------------------------------------------------------------------


@pytest.mark.parametrize("fake", [True, False])
async def test_a_revise_is_refused_feature_off_with_the_switch_off_writing_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch, fake: bool
) -> None:
    """The environment's switch, the only one since #824: off refuses even with every plan's
    route covering soundless (or the web's ``fake``)."""
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    monkeypatch.setattr(routes_module, "SOUNDLESS_SUITE_VERSION", running_suite_version())
    route(renv, *plan_keys(renv))
    gate(enabled=False, fake=fake)
    assert await connections_flag(renv) is False
    await refused_with(renv, class_id, base.id, "feature_off")
    assert fake_mowl.requests == []


@pytest.mark.parametrize(
    ("switch", "own", "others", "reason"),
    [
        (False, True, True, "feature_off"),  # the switch first
        (True, False, True, "soundless_not_evaluated"),  # then the job's own plan
        (True, False, False, "soundless_not_evaluated"),
        (True, True, False, "feature_off"),  # then every plan
        (True, True, True, None),
    ],
)
async def test_a_revise_checks_the_switch_then_its_own_plan_then_every_plan(
    renv: ReviseEnv,
    monkeypatch: pytest.MonkeyPatch,
    switch: bool,
    own: bool,
    others: bool,
    reason: str | None,
) -> None:
    class_id, design_job = await soundless_class(renv)  # designed under the web's fake
    [base] = renv.denv.revisions(design_job)
    gate(enabled=switch, fake=False)
    monkeypatch.setattr(routes_module, "SOUNDLESS_SUITE_VERSION", running_suite_version())
    keys, mine = plan_keys(renv), await own_key(renv)
    route(renv, *([mine] if own else []), *([k for k in keys if k != mine] if others else []))
    if reason is None:
        made = await ask(renv, class_id, base.id)
        assert made.status_code == 201, made.text
    else:
        await refused_with(renv, class_id, base.id, reason)


async def test_a_music_class_is_revised_whatever_the_soundless_gate_says(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, base = await renv.design()
    gate(enabled=False)
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    assert renv.denv.ops.row(job_id).state == "completed"


# --- the worker's backstops ------------------------------------------------------------------


async def test_a_queued_soundless_revise_whose_switch_went_off_ends_feature_off_calling_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    providers = len(renv.providers)
    renv.use("soundless_revise_good")
    made = await ask(renv, class_id, base.id)
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    gate(enabled=False)  # the worker runs with the environment's switch off
    await renv.run(design.REVISE_KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert len(renv.providers) == providers  # no model was even built
    assert fake_mowl.requests == []
    assert len(renv.revisions(class_id)) == 1
    assert renv.current(class_id) == base.id


async def test_a_soundless_revise_on_a_model_not_covering_soundless_ends_model_config(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    renv.use("soundless_revise_good")
    made = await ask(renv, class_id, base.id)
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    renv.soundless_covered = False
    renv.use("soundless_revise_good")
    await renv.run(design.REVISE_KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert all(p.sent == [] for p in renv.providers[1:])  # nothing sent to a model
    assert fake_mowl.requests == []
    assert len(renv.revisions(class_id)) == 1


# --- the music kind never changes ------------------------------------------------------------


async def test_a_revise_whose_class_changed_kind_before_the_store_stores_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth (a class has one open job at a time): the final store checks the class's
    ``music_source`` is still ``none``, or the job ends ``invalid_input`` and writes nothing."""
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    real = revise_mod.diff.summarize

    def flipped(*args: Any, **kwargs: Any) -> Any:
        renv.sql(
            "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
            m=json.dumps(SPOTIFY),
            c=class_id,
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(revise_mod.diff, "summarize", flipped)
    job_id, _ = await renv.revise(class_id, base.id, "soundless_revise_good")
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert len(renv.revisions(class_id)) == 1
    assert renv.current(class_id) == base.id
    assert [m.role for m in renv.messages(class_id)] == ["user"]  # the request only


async def test_a_soundless_base_that_is_no_longer_current_at_the_store_is_kept_but_not_current(
    renv: ReviseEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As for a music revise: the compare-and-set finds no row, the revision and the assistant
    message are kept and the current revision stays as the other writer left it (#584 N1)."""
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    real = revise_mod.run_stage

    async def move_then_run(*args: Any, **kwargs: Any) -> Any:
        renv.sql("UPDATE classes SET current_revision_id = NULL WHERE id = :c", c=class_id)
        return await real(*args, **kwargs)

    monkeypatch.setattr(revise_mod, "run_stage", move_then_run)
    job_id, _ = await renv.revise(class_id, base.id, "soundless_revise_good")
    assert renv.denv.ops.row(job_id).state == "completed"
    second = renv.revisions(class_id)[1]
    assert (second.parent_id, second.music) == (base.id, NONE)
    assert renv.current(class_id) is None  # unchanged: kept, not current
    assert [m.role for m in renv.messages(class_id)] == ["user", "assistant"]


async def test_an_unchanged_soundless_draft_fails_invalid_output(renv: ReviseEnv) -> None:
    """A revise that returns the base draft again stores nothing (#584 N2), as for music."""
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    job_id, _ = await renv.revise(class_id, base.id, "soundless_revise_unchanged")
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_output")
    assert len(renv.revisions(class_id)) == 1
    assert renv.current(class_id) == base.id
    assert [m.role for m in renv.messages(class_id)] == ["user"]  # the request only


async def test_a_music_revise_whose_class_became_soundless_before_the_store_stores_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The music store repeats the kind check (#584 N4), as the design store does: a class whose
    ``music_source`` turned ``none`` meanwhile ends ``invalid_input`` and writes nothing."""
    class_id, base = await renv.design()
    real = revise_mod.diff.summarize

    def flipped(*args: Any, **kwargs: Any) -> Any:
        renv.sql(
            "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
            m=json.dumps(NONE),
            c=class_id,
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(revise_mod.diff, "summarize", flipped)
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert len(renv.revisions(class_id)) == 1
    assert renv.current(class_id) == base
    assert [m.role for m in renv.messages(class_id)] == ["user"]


async def test_a_soundless_class_is_revised_against_its_layered_stated_values(
    renv: ReviseEnv,
) -> None:
    """#904: a soundless class that states values reads the request first, and the soundless
    revise stage gets the layered brief (here an intensity the request states)."""
    class_id, design_job = await soundless_class(renv)
    [base] = renv.denv.revisions(design_job)
    renv.state(class_id, ["length_min"])
    reading = {"intensity": {"value": "hard", "origin": "explicit"}}
    renv.use_interpreted("soundless_revise_good", reading)
    made = await ask(renv, class_id, base.id, "Make it hard.")
    assert made.status_code == 201, made.text
    await renv.run(design.REVISE_KIND)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    told, revised = (s.spec for s in renv.providers[-1].sent[:2])
    assert (told.stage, revised.stage, revised.system) == (
        "interpret",
        "revise",
        REVISE_SYSTEM_PROMPT,
    )
    assert '"intensity":"hard"}' in revised.first_message
    _, second = renv.revisions(class_id)
    assert second.provenance["stated"]["intensity"] == "hard"
