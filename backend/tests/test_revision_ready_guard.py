"""The ``revision_ready`` guard (ADR 0038 section 4, #1186): every route and job kind that takes a
revision id reaches ``classbeat.design.revision_ready.revision_ready`` or is on the reviewed
allowlist below with the reason it is safe, and every revision origin the source spells is
classified ready or not ready (``revision_ready_policy``). Each check is shown to fail on a
mutation: a route or kind that skips the call, a new one, and a new origin."""

from collections.abc import Iterable
from typing import Any, Final
from uuid import UUID

import pytest
from fastapi import APIRouter, FastAPI

import classbeat.web
import classbeat.worker.loop  # registers every job kind the worker runs
from authtools import make_settings
from classbeat.agent import interpretation_record, interpreted
from classbeat.classes import service
from classbeat.design import revise_sketch, revision_ready_policy
from classbeat.design.revision_ready import is_sketch
from classbeat.jobs import KINDS, Complete, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted
from revision_ready_scan import (
    origin_literals,
    reaches,
    revision_kinds,
    revision_routes,
    source_origins,
)

# The job kinds that refuse a sketch with ``not_ready`` before anything else (ADR 0038 section 4).
KINDS_THAT_CALL: Final = frozenset(
    {
        "save_workout",  # through saving.approval.revision
        "update_workout",  # through saving.approval.revision
        "send_to_player",  # through saving.approval.revision
        "refit_draft",
        "revise_songs",
        "studio_revise",
    }
)
# ``revise_draft`` takes a sketch as its base on purpose (ADR 0038 section 4: a chat change of the
# sketch is a sketch revise) and must tell a sketch base from a draft one.
KINDS_THAT_REVISE_A_SKETCH: Final = frozenset({"revise_draft"})
KINDS_ALLOWED: Final = {
    "swedebeat_match": "reachable for a composer song list; it writes nothing and sends only "
    "titles and artists through the owner's gateway, as for any song list today",
    "studio_design": "a studio song list only: unreachable for a non-studio class "
    "(swedebeat_studio.current_draft, music_kind.admit_studio)",
    "produce_outputs": "the studio outputs: unreachable for a non-studio class",
    "spotify_sync": "the studio's Spotify sync: unreachable for a non-studio class",
}

ROUTES_THAT_CALL: Final = frozenset(
    {
        ("POST", "/api/classes/{class_id}/current"),
        ("POST", "/api/classes/{class_id}/duplicate"),
        ("PUT", "/api/classes/{class_id}/beat-one/{song}"),
        ("DELETE", "/api/classes/{class_id}/beat-one/{song}"),
    }
)
_STUDIO: Final = "a studio class only: unreachable for a non-studio class (current_draft.py)"
ROUTES_ALLOWED: Final = {
    ("POST", "/api/jobs/{job_id}/approve"): "approves the waiting job's own song_list revision "
    "(jobs.approval.approve); a sketch is a draft and is never a job's song list",
    ("GET", "/api/classes/{class_id}/revisions/{revision_id}"): "a read: writes nothing and "
    "calls nothing outside classbeat",
    ("GET", "/api/classes/{class_id}/studio-songs/{revision_id}"): _STUDIO,
    ("POST", "/api/classes/{class_id}/studio-songs/{revision_id}/undo"): _STUDIO,
    ("POST", "/api/classes/{class_id}/studio-songs/{revision_id}/another"): _STUDIO,
    ("POST", "/api/classes/{class_id}/studio-songs/{revision_id}/swap-anyway"): _STUDIO,
    ("POST", "/api/classes/{class_id}/studio-songs/{revision_id}/design"): _STUDIO,
    ("POST", "/api/classes/{class_id}/outputs"): _STUDIO,
    ("POST", "/api/classes/{class_id}/outputs/{output}/{action}"): _STUDIO,
    ("GET", "/api/classes/{class_id}/spotify-sync/{revision_id}"): _STUDIO,
    ("POST", "/api/classes/{class_id}/spotify-sync/{revision_id}/{target}"): _STUDIO,
}

# Values the source spells as an ``origin`` that are not revision origins, with why.
NOT_REVISION_ORIGINS: Final = {
    **{
        value: "how one field of a request was read (agent.interpretation_record)"
        for value in interpretation_record.Origin.__args__
    },
    **{
        value: "a reading's origin (agent.interpreted)"
        for value in interpreted.StatedOrigin.__args__
    },
    "song_pick": "classes.music_source of a class on picked songs, not a revision's provenance",
}
# Modules whose ``origin`` is another word: Paddle's transaction origin (billing).
OTHER_ORIGIN_MODULES: Final = ("classbeat.billing.",)


def kind_problems(kinds: Iterable[JobKind]) -> list[str]:
    by_name = {k.name: k for k in kinds}
    problems: list[str] = []
    for name, fields in sorted(revision_kinds(by_name.values()).items()):
        kind = by_name[name]
        if name in KINDS_THAT_CALL:
            if kind.admit is None or not reaches(kind.admit):
                problems.append(f"{name} {fields}: admit does not reach revision_ready")
        elif name in KINDS_THAT_REVISE_A_SKETCH:
            if kind.admit is None or not reaches(kind.admit, is_sketch):
                problems.append(f"{name} {fields}: admit does not tell a sketch base")
        elif name not in KINDS_ALLOWED:
            problems.append(f"{name} {fields}: takes a revision id and is unclassified")
    return problems


def route_problems(routes: list[Any]) -> list[str]:
    problems: list[str] = []
    found = revision_routes(routes)
    endpoints = _endpoints(routes)
    for key, why in sorted(found.items()):
        if key in ROUTES_THAT_CALL:
            if not reaches(endpoints[key]):
                problems.append(f"{key} {why}: does not reach revision_ready")
        elif key not in ROUTES_ALLOWED:
            problems.append(f"{key} {why}: takes a revision id and is unclassified")
    return problems


def _endpoints(routes: list[Any]) -> dict[tuple[str, str], Any]:
    from fastapi.routing import APIRoute, iter_route_contexts

    out: dict[tuple[str, str], Any] = {}
    for context in iter_route_contexts(routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                out[(method, context.path or route.path)] = route.endpoint
    return out


def origin_problems(found: dict[str, set[str]]) -> list[str]:
    classified = revision_ready_policy.READY_ORIGINS | revision_ready_policy.NOT_READY_ORIGINS
    problems: list[str] = []
    for value, modules in sorted(found.items()):
        if value in NOT_REVISION_ORIGINS or value in classified:
            continue
        kept = sorted(m for m in modules if not m.startswith(OTHER_ORIGIN_MODULES))
        if kept:
            problems.append(f"origin {value!r} ({', '.join(kept)}) is neither ready nor not ready")
    return problems


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return classbeat.web.create_app(make_settings("postgresql+psycopg://u:p@127.0.0.1:1/none"))


def test_every_kind_that_takes_a_revision_id_is_classified_and_reaches_the_rule() -> None:
    assert kind_problems(KINDS) == []


def test_the_kinds_found_are_exactly_the_classified_ones() -> None:
    """A stale entry is a finding too: every name in the tables is a kind that takes an id."""
    classified = KINDS_THAT_CALL | KINDS_THAT_REVISE_A_SKETCH | set(KINDS_ALLOWED)
    assert set(revision_kinds(KINDS)) == classified


def test_every_route_that_takes_a_revision_id_is_classified_and_reaches_the_rule(
    app: FastAPI,
) -> None:
    assert route_problems(app.routes) == []


def test_the_routes_found_are_exactly_the_classified_ones(app: FastAPI) -> None:
    """The detector finds bodies parsed by hand (duplicate, approve, the outputs' actions) and
    query parameters (the beat-one clear), and nothing in the tables is stale."""
    found = set(revision_routes(app.routes))
    assert found == ROUTES_THAT_CALL | set(ROUTES_ALLOWED)
    assert ("POST", "/api/classes/{class_id}/duplicate") in found
    assert ("POST", "/api/jobs/{job_id}/approve") in found


def test_every_origin_in_the_source_is_classified() -> None:
    found = source_origins()
    assert origin_problems(found) == []
    assert revision_ready_policy.SKETCH in found  # the scan sees the new origin
    classified = revision_ready_policy.READY_ORIGINS | revision_ready_policy.NOT_READY_ORIGINS
    assert classified - {None} <= set(found)  # and the policy lists no origin nobody makes


def test_only_a_sketch_is_not_ready() -> None:
    assert {"sketch"} == revision_ready_policy.NOT_READY_ORIGINS
    assert revision_ready_policy.refusal("sketch") == "not_ready"
    assert revision_ready_policy.refusal(None) is None
    assert revision_ready_policy.refusal("design_draft") is None
    assert revision_ready_policy.refusal("brand_new") == "not_ready"  # unknown: fail closed


# --- the mutations ----------------------------------------------------------------------------


def test_a_route_whose_call_is_removed_fails(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_the_rule(*_: Any) -> None:
        return None

    monkeypatch.setattr(service, "revision_ready", not_the_rule)
    assert route_problems(app.routes) == [
        "('POST', '/api/classes/{class_id}/current') ['PickIn:revision_id', 'body:revision_id']: "
        "does not reach revision_ready"
    ]


def test_a_new_route_that_takes_a_revision_id_fails() -> None:
    router = APIRouter()

    @router.post("/api/classes/{class_id}/publish/{revision_id}")
    async def publish(class_id: str, revision_id: str) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"published": revision_id}

    problems = route_problems(router.routes)
    assert len(problems) == 1
    assert "unclassified" in problems[0]


class _NewInput(JobInput):
    revision_id: UUID


async def _admit_nothing(*_: Any) -> Admitted:
    return Admitted()


async def _run_nothing(_: JobRun) -> Complete:
    raise AssertionError("never run")


def test_a_new_kind_that_takes_a_revision_id_fails() -> None:
    new = JobKind("publish_revision", _NewInput, ("publish",), _run_nothing, admit=_admit_nothing)
    problems = kind_problems([*KINDS, new])
    assert problems == ["publish_revision ['revision_id']: takes a revision id and is unclassified"]


def test_a_kind_whose_call_is_removed_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_the_rule(*_: Any) -> None:
        return None

    from classbeat.design import refit

    monkeypatch.setattr(refit, "revision_ready", not_the_rule)
    assert kind_problems(KINDS) == [
        "refit_draft ['base_revision_id']: admit does not reach revision_ready"
    ]


def test_a_revise_that_stops_telling_a_sketch_base_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_the_check(*_: Any) -> bool:
        return False

    monkeypatch.setattr(revise_sketch, "is_sketch", not_the_check)
    assert kind_problems(KINDS) == [
        "revise_draft ['base_revision_id']: admit does not tell a sketch base"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "x = {'origin': 'brand_new'}",
        "build(result, origin='brand_new')",
        "class P:\n    origin: Literal['design_draft', 'brand_new'] | None = None",
        "Q = text(\"SELECT 1 WHERE provenance ->> 'origin' IN ('revise_draft', 'brand_new')\")",
        "if row.origin == 'brand_new':\n    pass",
        "if row.origin in ('revise_draft', 'brand_new'):\n    pass",
        "if row.origin not in {'brand_new'}:\n    pass",
        "Q = text(\"SELECT 1 WHERE provenance ->> 'origin' NOT IN ('brand_new')\")",
        "Q = text(\"SELECT 1 WHERE provenance ->> 'origin' <> 'brand_new'\")",
        "Q = text(\"SELECT 1 WHERE r.provenance ->> 'origin' IS DISTINCT FROM 'brand_new'\")",
        "ORIGIN: Final = 'brand_new'",
    ],
)
def test_a_new_origin_that_is_unclassified_fails(source: str) -> None:
    found = {value: {"classbeat.example"} for value in origin_literals(source)}
    assert origin_problems(found) == [
        "origin 'brand_new' (classbeat.example) is neither ready nor not ready"
    ]
