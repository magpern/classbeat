"""Soundless classes through the jobs API and the worker side (ADR 0007 amendment of 2026-11-03,
items 1 to 7; SL1, #527), against Postgres as ``classbeat_app``, the fake MOWL and
``FakeProvider`` scripts. Never the network, never a real account or model.

Covered: a soundless ``design_draft`` completes on the fakes with no MOWL request and no ``music``
stage, storing ``{"source": "none"}`` as the revision's music and the class's ``music_source``;
the model sees no ``music`` key and no music text; the three-condition gate (every path that must
leave the effective flag off and ``admit`` refusing, with no job and no credit), the order of
``admit``'s checks, and the effective flag ``GET /api/connections`` returns; the worker's
backstops (``feature_off``, ``model_config``) before any call; the music kind never changing, at
``admit`` and in the final store, both ways. The revise is ``test_soundless_revise.py`` (SL2);
Save and Update are ``test_save_soundless.py`` (SL3); here only that a class this path designs
is admitted for Save (#586).
"""

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from classbeat import design
from classbeat.agent.records import Provenance
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.soundless import SYSTEM_PROMPT
from classbeat.agent.stage import prompt_version
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
MUSIC_WORDS = ("playlist", "track", "bpm", "drop", "breakdown", "section", "beat", "song")


def gate(*, enabled: bool = True, fake: bool = True) -> None:
    music_kind.configure(music_kind.SoundlessGate(enabled=enabled, fake_covers=fake))


def admin_flag(renv: ReviseEnv, value: Any, *, raw: str | None = None) -> None:
    """The stored ``soundless_enabled``: ``value`` as JSON, ``raw`` a JSON text (``null``, as
    the admin's DELETE leaves it), or no row for ``None``."""
    renv.sql("DELETE FROM app_settings WHERE key = 'soundless_enabled'")
    if value is not None or raw is not None:
        renv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('soundless_enabled', CAST(:v AS jsonb))",
            v=raw if raw is not None else json.dumps(value),
        )


@pytest.fixture(autouse=True)
def soundless_on(renv: ReviseEnv) -> Iterator[None]:
    """The environment's switch on, the web's ``fake`` covering (as the e2e stack) and the admin
    flag stored ``on``; after ``renv``, whose app configured the gate from its settings (off)."""
    gate()
    admin_flag(renv, "on")
    try:
        yield
    finally:
        music_kind.configure(None)
        admin_flag(renv, None)
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


async def connections_flag(renv: ReviseEnv) -> bool:
    got = await renv.browser.get("/api/connections")
    assert got.status_code == 200, got.text
    return got.json()["soundless_enabled"]


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


# --- the gate at creation -------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", ["environment_off", "nothing_stored", "deleted", "invalid", "stored_off"]
)
async def test_every_switch_path_leaves_the_flag_off_and_admit_refusing_feature_off(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, case: str
) -> None:
    """Item 2: on only with the environment on **and** a stored ``on``. An environment-only on,
    nothing stored, the admin's DELETE (JSON ``null``), an invalid or an ``off`` value: off."""
    if case == "environment_off":
        gate(enabled=False)  # the database can never switch it on past the environment
    elif case == "nothing_stored":
        admin_flag(renv, None)
    elif case == "deleted":
        admin_flag(renv, None, raw="null")
    elif case == "invalid":
        admin_flag(renv, "yes")
    else:
        admin_flag(renv, "off")
    assert await connections_flag(renv) is False
    await refused_with(renv, "feature_off")
    assert fake_mowl.requests == []
    cycling = await create(renv, design_body(renv, SPOTIFY))  # music never reads the flag
    assert cycling.status_code == 201, cycling.text


async def test_the_flag_needs_every_plans_route_to_cover_soundless_read_on_every_request(
    renv: ReviseEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 6: without the web's own ``fake``, the effective flag needs a covering ``design``
    route for every plan's ``route_key``, checked on each read: a result below
    ``SOUNDLESS_SUITE_VERSION`` (or with it ``None``, as in SL1) covers nothing, and a route
    cleared later or a plan added later turns it off with nothing stored."""
    gate(fake=False)
    keys = plan_keys(renv)
    route(renv, *keys)
    first = routes_module.SOUNDLESS_SUITE_VERSION
    assert first is not None
    assert running_suite_version() >= first  # SL5 (#531)
    # The SL1 state: SOUNDLESS_SUITE_VERSION is None, so even a fresh passing route covers nothing.
    monkeypatch.setattr(routes_module, "SOUNDLESS_SUITE_VERSION", None)
    assert await connections_flag(renv) is False
    await refused_with(renv, "soundless_not_evaluated")
    # A result recorded on a suite version below the soundless one.
    monkeypatch.setattr(routes_module, "SOUNDLESS_SUITE_VERSION", running_suite_version() + 1)
    assert await connections_flag(renv) is False
    await refused_with(renv, "soundless_not_evaluated")
    # SL5's state: every plan covered, so the flag is on and a design is admitted.
    monkeypatch.setattr(routes_module, "SOUNDLESS_SUITE_VERSION", running_suite_version())
    assert await connections_flag(renv) is True
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    renv.sql("DELETE FROM jobs WHERE owner_id = :o", o=renv.owner)
    renv.sql("DELETE FROM credit_ledger WHERE owner_id = :o", o=renv.owner)
    # Another plan's route cleared later: the user's own plan is covered, the flag is not.
    mine = await own_key(renv)
    other = next(k for k in keys if k != mine)
    renv.sql("DELETE FROM model_routes WHERE route_key = :k", k=other)
    assert await connections_flag(renv) is False
    await refused_with(renv, "feature_off")
    route(renv, other)
    assert await connections_flag(renv) is True
    # A plan added later, with a route key of its own and no route.
    renv.sql(
        "INSERT INTO plans (id, name, rank, classes_per_month, revisions_per_class, "
        "concurrent_jobs, monthly_cost_micros, route_key) "
        "VALUES ('later', 'Later', 99, 3, 3, 1, 10000000, 'later')"
    )
    try:
        assert await connections_flag(renv) is False
        await refused_with(renv, "feature_off")
    finally:
        renv.sql("DELETE FROM plans WHERE id = 'later'")
    # The user's own plan's route cleared later.
    renv.sql("DELETE FROM model_routes WHERE route_key = :k", k=mine)
    assert await connections_flag(renv) is False
    await refused_with(renv, "soundless_not_evaluated")


@pytest.mark.parametrize(
    ("switch", "own", "others", "reason"),
    [
        (False, False, False, "feature_off"),  # 1. the switch first, whatever is covered
        (False, True, True, "feature_off"),
        (True, False, True, "soundless_not_evaluated"),  # 2. the job's own plan next
        (True, False, False, "soundless_not_evaluated"),  # (before every plan, or 2 is dead)
        (True, True, False, "feature_off"),  # 3. then every plan
        (True, True, True, None),
    ],
)
async def test_admit_checks_the_switch_then_its_own_plan_then_every_plan(
    renv: ReviseEnv,
    monkeypatch: pytest.MonkeyPatch,
    switch: bool,
    own: bool,
    others: bool,
    reason: str | None,
) -> None:
    gate(fake=False)
    monkeypatch.setattr(routes_module, "SOUNDLESS_SUITE_VERSION", running_suite_version())
    admin_flag(renv, "on" if switch else "off")
    keys, mine = plan_keys(renv), await own_key(renv)
    route(
        renv,
        *([mine] if own else []),
        *([k for k in keys if k != mine] if others else []),
    )
    if reason is None:
        made = await create(renv, design_body(renv))
        assert made.status_code == 201, made.text
        assert counts(renv)[2] == 1  # the credit is reserved only once admitted
    else:
        await refused_with(renv, reason)


# --- the worker's backstops -----------------------------------------------------------------


async def test_a_queued_soundless_design_whose_switch_went_off_ends_feature_off_calling_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    renv.use("soundless_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    admin_flag(renv, None)  # the admin cleared it while the job waited
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert renv.providers == []  # no model was even built
    assert fake_mowl.requests == []
    assert renv.denv.revisions(job_id) == []


async def test_a_soundless_design_on_a_model_not_covering_soundless_ends_model_config(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    renv.use("soundless_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    renv.soundless_covered = False
    renv.use("soundless_first_draft")
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert all(p.sent == [] for p in renv.providers)  # nothing sent to a model
    assert fake_mowl.requests == []
    renv.soundless_covered = True
    cycling, _ = await renv.design()  # a music class never needs the soundless coverage
    assert cycling


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
