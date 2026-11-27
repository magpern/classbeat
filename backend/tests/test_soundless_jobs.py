"""Soundless classes through the jobs API and the worker side (ADR 0007 amendment of 2026-11-03,
items 1 to 7; SL1, #527), against Postgres as ``classbeat_app``, the fake MOWL and
``FakeProvider`` scripts. Never the network, never a real account or model.

Covered: a soundless ``design_draft`` completes on the fakes with no MOWL request and no ``music``
stage, storing ``{"source": "none"}`` as the revision's music and the class's ``music_source``;
the model sees no ``music`` key and no music text; no switch and no coverage refuses it at
``admit`` (ADR 0007 amendment of 2026-11-27, #1543), and ``GET /api/connections`` carries no flag;
the worker serves an uncovered model with the stale-route warning (ADR 0012 section 5) and still
ends an unusable route ``model_config`` before any call; the music kind never changing, at
``admit`` and in the final store, both ways. The revise is ``test_soundless_revise.py`` (SL2);
Save and Update are ``test_save_soundless.py`` (SL3); here only that a class this path designs
is admitted for Save (#586).
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from agentfake import RefusingModels
from classbeat import design
from classbeat.agent.records import Provenance
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.soundless import SYSTEM_PROMPT
from classbeat.agent.stage import prompt_version
from classbeat.design import kind as design_kind
from classbeat.design import music_kind, revision
from classbeat.models import routes as routes_module
from classbeat.models.versions import all_prompt_versions, running_suite_version
from designtools import BRIEF, FakeMowlMusic
from jobapi import new_key
from jobtools import new_job
from modeltools import add_result, clear_gate
from optools import PLAYLIST
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

NONE: dict[str, str] = {"source": "none"}
SPOTIFY: dict[str, str] = {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST}
# Words of music that may never reach a soundless model (ADR 0007 amendment, item 5).
# "section" is not here: it is the ride shape's own word (`Segment.section`, #1493), not music.
MUSIC_WORDS = ("playlist", "track", "bpm", "drop", "breakdown", "beat", "song")


def gate(*, fake: bool = True) -> None:
    music_kind.configure(music_kind.SoundlessGate(fake_covers=fake))


@pytest.fixture(autouse=True)
def soundless_on(renv: ReviseEnv) -> Iterator[None]:
    """The web's ``fake`` covering (as the e2e stack); there is no switch (#1543) and nothing is
    stored (there is no admin flag since #824). After ``renv``, whose app configured the setting
    from its settings."""
    gate()
    try:
        yield
    finally:
        music_kind.configure(None)
        clear_gate(renv.denv.ops.admin)


def design_body(renv: ReviseEnv, music: dict[str, str] = NONE, **more: Any) -> dict[str, Any]:
    values = {
        "title": "Quiet Tuesday",
        "brief": BRIEF,
        "music": music,
        "mowl_connection_id": str(renv.browser.connection_id),
    }
    return {"kind": design.KIND, "input": values | more}


async def create(renv: ReviseEnv, body: dict[str, Any]) -> Any:
    return await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


def counts(renv: ReviseEnv) -> tuple[int, int, int]:
    """(classes, jobs, credit ledger rows) of the user: what a refused create must not add to."""
    o = renv.owner
    return (
        renv.sql("SELECT count(*) FROM classes WHERE owner_id = :o", o=o).scalar_one(),
        renv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=o).scalar_one(),
        renv.sql("SELECT count(*) FROM credit_ledger WHERE owner_id = :o", o=o).scalar_one(),
    )


async def refused_with(renv: ReviseEnv, reason: str, body: dict[str, Any] | None = None) -> None:
    before = counts(renv)
    refused = await create(renv, body or design_body(renv))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == reason
    assert counts(renv) == before  # no class, no job, no credit


async def soundless_class(renv: ReviseEnv) -> tuple[UUID, UUID]:
    """A soundless class designed by the worker: (class id, job id)."""
    renv.use("soundless_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    return UUID(made.json()["class_id"]), job_id


def route(renv: ReviseEnv, *keys: str) -> None:
    """A passing design-group result with the running code's prompt versions and a ``design``
    route at it for each of ``keys`` (setup, as the superuser)."""
    result = add_result(renv.denv.ops.admin)
    for key in keys:
        renv.sql(
            "INSERT INTO model_routes (route_key, stage_group, provider, model, effort, "
            "eval_result_id, set_at, set_by, gate_passed) SELECT :k, 'design', provider, model, "
            "effort, id, now(), 'setup', true FROM model_eval_results WHERE id = :r",
            k=key,
            r=result,
        )


def plan_keys(renv: ReviseEnv) -> list[str]:
    return list(renv.sql("SELECT DISTINCT route_key FROM plans ORDER BY 1").scalars().all())


async def own_key(renv: ReviseEnv) -> str:
    """The ``route_key`` of the user's effective plan (what ``with_plan`` gives the job)."""
    got = await renv.browser.get("/api/account/plan")
    assert got.status_code == 200, got.text
    plan = got.json()["plan"]
    return renv.sql("SELECT route_key FROM plans WHERE id = :p", p=plan).scalar_one()


# --- a soundless design end to end ----------------------------------------------------------


async def test_a_soundless_design_completes_with_no_music_call_and_stores_no_music(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, job_id = await soundless_class(renv)
    assert fake_mowl.requests == []  # no attach, no listing, no analysis: nothing at MOWL
    stages = renv.sql(
        "SELECT payload ->> 'stage' FROM job_events WHERE job_id = :j AND kind = 'stage_started' "
        "ORDER BY seq",
        j=job_id,
    ).scalars()
    assert list(stages) == ["design"]  # no music stage
    [rev] = renv.denv.revisions(job_id)
    assert rev.music == NONE
    content = WorkoutDraft.model_validate(rev.content)  # Save and the page read it unchanged
    rpms = [i.rpm for s in content.segments for i in s.intervals]
    assert all(isinstance(r, list) for r in rpms)  # never "beat"
    made = Provenance.model_validate_json(json.dumps(rev.provenance))
    assert (made.map_version, made.crossfade_sec, made.sport) == (None, None, None)
    assert made.origin == "design_draft"
    assert made.prompt_version == all_prompt_versions()["soundless_design"]
    stored = renv.sql(
        "SELECT music_source, brief, current_revision_id FROM classes WHERE id = :c", c=class_id
    ).one()
    assert stored.music_source == NONE
    assert stored.brief["sport"] == "cycling"
    assert stored.current_revision_id == rev.id
    # What the model was sent: the soundless prompt, two tools, no music key, no music words.
    [provider] = renv.providers
    [sent] = provider.sent
    assert sent.spec.system == SYSTEM_PROMPT
    assert [t.name for t in sent.spec.tools] == ["preview_draft", "submit_draft"]
    payload = json.loads(sent.spec.first_message.split("\n\n", 1)[1])
    assert set(payload) == {"brief"}
    told = sent.spec.first_message + json.dumps([r for t in sent.turns for r in t[0]], default=str)
    assert not any(word in told.lower() for word in ("playlist", "track", "bpm", "drop"))
    assert prompt_version(sent.spec.system, sent.spec.tools) == made.prompt_version


async def test_a_class_designed_without_music_is_admitted_for_save(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    """#586 (SL3 review): the class the real SL1 path stores (``music_source`` and revision music
    ``none``, no link) is admitted by Save's approval, not only the rows ``test_save_soundless``
    inserts, so the two cannot drift apart. Creation only: the write is that file's."""
    class_id, job_id = await soundless_class(renv)
    [rev] = renv.denv.revisions(job_id)
    save = {
        "kind": "save_workout",
        "input": {
            "class_id": str(class_id),
            "revision_id": str(rev.id),
            "content_hash": bytes(rev.content_hash).hex(),
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    made = await create(renv, save)
    assert made.status_code == 201, made.text
    assert made.json()["kind"] == "save_workout"
    assert fake_mowl.requests == []  # a created job calls nothing; no link was ever needed


# --- no switch, and the model's coverage (#1543) -------------------------------------------


@pytest.mark.parametrize(
    ("version", "own", "others"),
    [
        (None, False, False),  # SL1's state: nothing can cover soundless
        ("below", True, True),  # results below the soundless suite version cover nothing
        ("current", False, True),  # the job's own plan uncovered
        ("current", True, False),  # another plan uncovered
        ("current", False, False),  # no route at all: the bootstrap choice
        ("current", True, True),
    ],
)
async def test_no_music_is_admitted_with_no_switch_whatever_the_routes_cover(
    renv: ReviseEnv,
    fake_mowl: FakeMowlMusic,
    monkeypatch: pytest.MonkeyPatch,
    version: str | None,
    own: bool,
    others: bool,
) -> None:
    """ADR 0007 amendment of 2026-11-27 (#1543; owner, 2026-11-26: "No music" is always a choice):
    no switch and no coverage refuses a soundless design at ``admit``. Before, the switch off was
    ``feature_off`` and an uncovered plan ``soundless_not_evaluated`` (#527, #824); this replaces
    those cases, it does not drop them: every coverage state that used to refuse is admitted here
    with its one credit, and the worker decides the model (the tests below)."""
    gate(fake=False)
    suite = {None: None, "below": running_suite_version() + 1, "current": running_suite_version()}
    monkeypatch.setattr(routes_module, "SOUNDLESS_SUITE_VERSION", suite[version])
    keys, mine = plan_keys(renv), await own_key(renv)
    route(
        renv,
        *([mine] if own else []),
        *([k for k in keys if k != mine] if others else []),
    )
    before = counts(renv)
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    assert counts(renv) == (before[0] + 1, before[1] + 1, before[2] + 1)  # one class, job, credit
    assert fake_mowl.requests == []


async def test_the_connections_view_carries_no_soundless_flag_and_no_key_can_be_stored(
    renv: ReviseEnv,
) -> None:
    """#1543: ``GET /api/connections`` has no ``soundless_enabled`` (the web always offers "No
    music"), and the retired admin key still cannot be stored (the narrowed check of migration
    0043, #824)."""
    gate(fake=False)
    got = await renv.browser.get("/api/connections")
    assert got.status_code == 200, got.text
    assert "soundless_enabled" not in got.json()
    with pytest.raises(IntegrityError):
        renv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('soundless_enabled', '\"on\"'::jsonb)"
        )


async def test_a_soundless_design_on_an_uncovered_model_is_served_with_the_stale_route_warning(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0012 section 5 (#1543): a model with no recorded soundless coverage designs the class
    with the soundless prompt, as a stale route serves other stages, and the worker logs the
    warning once per process and route key (codes only). Before, this was ``model_config``."""
    warned_keys = music_kind._warned  # pyright: ignore[reportPrivateUsage]
    warned_keys.clear()
    renv.soundless_covered = False
    try:
        with caplog.at_level(logging.WARNING, logger="classbeat.design.music_kind"):
            first, job_id = await soundless_class(renv)
            second, _ = await soundless_class(renv)
        [rev] = renv.denv.revisions(job_id)
        assert rev.music == NONE
        made = Provenance.model_validate_json(json.dumps(rev.provenance))
        assert made.prompt_version == all_prompt_versions()["soundless_design"]
        assert [p.spec.system for p in renv.providers[-1].sent] == [SYSTEM_PROMPT]
        warned = [r for r in caplog.records if r.getMessage() == "model route stale"]
        assert len(warned) == 1  # once per process and route key, not per job
        assert (warned[0].__dict__["reason"], warned[0].__dict__["stage_group"]) == (
            music_kind.UNCOVERED,
            "design",
        )
        assert warned[0].__dict__["route_key"] is None  # the fake's config: no route
        assert first != second
        assert fake_mowl.requests == []
    finally:
        renv.soundless_covered = True
        warned_keys.clear()


async def test_a_covered_soundless_design_logs_no_warning(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, caplog: pytest.LogCaptureFixture
) -> None:
    music_kind._warned.clear()  # pyright: ignore[reportPrivateUsage]
    with caplog.at_level(logging.WARNING, logger="classbeat.design.music_kind"):
        await soundless_class(renv)
    assert [r for r in caplog.records if r.getMessage() == "model route stale"] == []


async def test_an_unusable_route_still_ends_a_soundless_design_model_config(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    """The warning replaces only the coverage refusal: a route the worker may not use (a newer
    failure, no key: ``RouteUnusable``) is still ``model_config`` before any call."""
    renv.use("soundless_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    runtime = design_kind.current_runtime()
    assert runtime is not None
    design.configure(replace(runtime, models=RefusingModels()))
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert fake_mowl.requests == []
    assert renv.denv.revisions(job_id) == []


# --- the music kind never changes -----------------------------------------------------------


async def test_the_music_kind_of_a_class_never_changes_at_admit(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    music, _ = await renv.design()
    await refused_with(
        renv, "invalid_input", design_body(renv, NONE, class_id=str(music), title=None)
    )
    quiet, _ = await soundless_class(renv)
    await refused_with(
        renv, "invalid_input", design_body(renv, SPOTIFY, class_id=str(quiet), title=None)
    )
    same = await create(renv, design_body(renv, NONE, class_id=str(quiet), title=None))
    assert same.status_code == 201, same.text


@pytest.mark.parametrize("first", ["music", "soundless"])
async def test_a_design_of_the_other_kind_admitted_before_cannot_store(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, first: str
) -> None:
    """A class has one open job at a time, so this is defence in depth: a job of the other kind
    inserted past ``admit`` fails its final store ``invalid_input`` and stores nothing."""
    if first == "music":
        class_id, _ = await renv.design()
        music, script = NONE, "soundless_first_draft"
    else:
        class_id, _ = await soundless_class(renv)
        music, script = SPOTIFY, "good_first_draft"
    values = design_body(renv, music, class_id=str(class_id))["input"]
    del values["title"]
    job_id = new_job(
        renv.denv.ops.admin, renv.owner, class_id, kind=design.KIND, input=json.dumps(values)
    )
    renv.use(script)
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert len(renv.revisions(class_id)) == 1
    source = renv.sql("SELECT music_source FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert music_kind.is_none(source) == (first == "soundless")


@pytest.mark.parametrize("kind", ["music", "soundless"])
async def test_the_design_stores_check_the_music_kind_before_adopt_first(
    renv: ReviseEnv,
    fake_mowl: FakeMowlMusic,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """#589: a class with no current revision, whose ``music_source`` the superuser sets to the
    other kind after admit, is refused ``invalid_input`` BEFORE ``adopt_first`` runs. The store is
    one transaction that rolls back, so the result alone cannot show the order: the spy shows that
    ``adopt_first`` was never reached, and nothing is written (no revision, no events of a
    revision, the source as seeded)."""
    if kind == "music":
        asked, seeded, script = SPOTIFY, NONE, "good_first_draft"
    else:
        asked, seeded, script = NONE, SPOTIFY, "soundless_first_draft"
    renv.use(script)
    made = await create(renv, design_body(renv, asked))
    assert made.status_code == 201, made.text
    job_id, class_id = UUID(made.json()["id"]), UUID(made.json()["class_id"])
    renv.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=json.dumps(seeded),
        c=class_id,
    )
    before = renv.sql(
        "SELECT current_revision_id, brief FROM classes WHERE id = :c", c=class_id
    ).one()
    assert before.current_revision_id is None
    reached: list[bool] = []
    original = revision.adopt_first

    async def spy(*args: Any, **kwargs: Any) -> bool:
        reached.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(revision, "adopt_first", spy)
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert reached == []  # refused before adopt_first
    assert renv.revisions(class_id) == []
    assert renv.denv.revisions(job_id) == []
    after = renv.sql(
        "SELECT music_source, current_revision_id, brief FROM classes WHERE id = :c", c=class_id
    ).one()
    assert (after.music_source, after.current_revision_id, after.brief) == (
        seeded,
        None,
        before.brief,
    )


@pytest.mark.parametrize("source", [None, NONE])
async def test_the_soundless_store_checks_strictly_after_adopt_first(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, source: dict[str, str] | None
) -> None:
    """#589 (review finding 2 of #621): the ``before_adopt`` check accepts an unset source, and
    ``adopt_first`` leaves a class that already has a current revision as it is, so a class with
    a current revision and a NULL ``music_source`` (an older class; ``admit_music`` refuses it at
    creation, so the superuser sets it past admit) reaches only the strict check after
    ``adopt_first``: ``invalid_input``, nothing written. The control (``none``) stores."""
    class_id, _ = await soundless_class(renv)
    [first] = renv.revisions(class_id)
    values = design_body(renv, NONE, class_id=str(class_id))["input"]
    del values["title"]
    job_id = new_job(
        renv.denv.ops.admin, renv.owner, class_id, kind=design.KIND, input=json.dumps(values)
    )
    renv.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=None if source is None else json.dumps(source),
        c=class_id,
    )
    renv.use("soundless_first_draft")
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    after = renv.sql(
        "SELECT music_source, current_revision_id FROM classes WHERE id = :c", c=class_id
    ).one()
    assert (after.music_source, after.current_revision_id) == (source, first.id)
    if source is not None:
        assert (row.state, row.error_category) == ("completed", None)
        assert len(renv.revisions(class_id)) == 2
        return
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert renv.revisions(class_id) == [first]
    assert renv.denv.revisions(job_id) == []


async def test_a_running_brief_with_no_music_is_refused_at_input(renv: ReviseEnv) -> None:
    body = design_body(renv)
    body["input"]["brief"] = {"sport": "running", "length_min": 30}
    refused = await create(renv, body)
    assert refused.status_code == 422, refused.text
    assert counts(renv) == (0, 0, 0)


async def test_the_class_list_and_detail_say_which_class_is_soundless(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    """``ClassOut.soundless`` (item 10, SL4 #530): true only for a ``none`` source; false for a
    music class and for a class with no source yet."""
    music, _ = await renv.design()
    quiet, _ = await soundless_class(renv)
    empty = renv.sql(
        "INSERT INTO classes (owner_id, title) VALUES (:o, 'No source yet') RETURNING id",
        o=renv.owner,
    ).scalar_one()
    listed = await renv.browser.get("/api/classes")
    assert listed.status_code == 200, listed.text
    flags = {UUID(c["id"]): c["soundless"] for c in listed.json()["classes"]}
    assert flags == {music: False, quiet: True, empty: False}
    for class_id, expected in [(music, False), (quiet, True), (empty, False)]:
        shown = await renv.browser.get(f"/api/classes/{class_id}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["soundless"] is expected
