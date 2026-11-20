"""The contract test of SD-P4 (#1301, ADR 0040 section 10): classbeat's spotdown client and the fake
spotdown (``spotdownfake``) against spotdown's versioned schemas, vendored byte for byte under
``tests/contracts/spotdown`` (``SOURCE.md`` names the commit).

- the vendored examples validate against their schemas, and the client's parsers accept each;
- every answer the fake gives in a run over every per-track status and job outcome validates
  against ``match-v1``, ``job-v1``, ``lookup-v1`` and ``analysis-v1``;
- every request body the client sends validates against the request shapes of
  ``docs/contract-v2.md`` (written here as schemas: the contract has no request schema file);
- the parsers and the schemas agree on a table of broken answers (each refused by both).
"""

import copy
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from classbeat.spotdown.answer_model import Uploaded, parse_analysis, parse_job, parse_lookup
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import Pending
from classbeat.spotdown.match_model import parse_match_result
from classbeat.spotdown.request_model import Reference, TrackQuery
from classbeat.spotdown.scope_calc import idempotency_key
from spotdownfake import CONTRACTS, ENDS, MATCH_STATUSES, SCOPE_A, FakeSpotdown, example, spotdown

__all__ = ["spotdown"]  # the fixture

SCHEMAS = {
    name: json.loads((CONTRACTS / f"{name}.json").read_text("utf-8"))
    for name in (
        "analysis-v1",
        "match-v1",
        "job-v1",
        "lookup-v1",
    )
}
VALIDATORS = {name: Draft202012Validator(schema) for name, schema in SCHEMAS.items()}
_SCOPE = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}
MATCH_REQUEST = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tracks"],
    "properties": {
        "cache_scope": _SCOPE,
        "tracks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["client_ref", "title", "artists", "duration_s"],
                "properties": {
                    "client_ref": {"type": "string", "minLength": 1, "maxLength": 128},
                    "title": {"type": "string", "minLength": 1},
                    "artists": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "duration_s": {"type": "number", "minimum": 0},
                    "isrc": {"type": "string"},
                },
            },
        },
    },
}
ANALYSIS_REQUEST = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cache_scope", "client_ref"],
    "oneOf": [{"required": ["upload_id"]}, {"required": ["source_ref"]}],
    "properties": {
        "cache_scope": _SCOPE,
        "client_ref": {"type": "string", "minLength": 1, "maxLength": 128},
        "upload_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
        "source_ref": {"type": "string", "pattern": "^[a-z]+:.+$", "maxLength": 256},
        "reference": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "id", "duration_s"],
            "properties": {
                "kind": {"type": "string", "minLength": 1, "maxLength": 64},
                "id": {"type": "string", "minLength": 1, "maxLength": 256},
                "duration_s": {"type": "number", "minimum": 0},
            },
        },
    },
}


def errors(schema: str, value: Any) -> list[str]:
    return [e.message for e in VALIDATORS[schema].iter_errors(value)]


def raw(value: object) -> bytes:
    return json.dumps(value).encode()


@pytest.mark.parametrize("schema", sorted(SCHEMAS))
def test_the_vendored_schemas_are_valid_draft_2020_12(schema: str) -> None:
    Draft202012Validator.check_schema(SCHEMAS[schema])


@pytest.mark.parametrize(
    "path", sorted((CONTRACTS / "examples").glob("*.json")), ids=lambda p: p.name
)
def test_every_example_validates_and_the_client_parses_it(path: Any) -> None:
    value = json.loads(path.read_text("utf-8"))
    schema = path.name.split(".")[0]
    assert errors(schema, value) == []
    parsed = {
        "analysis-v1": lambda: parse_analysis(raw(value)),
        "match-v1": lambda: parse_match_result(value),
        "job-v1": lambda: parse_job(raw(value)),
        "lookup-v1": lambda: parse_lookup(raw(value)),
    }[schema]()
    assert parsed is not None


async def chunks() -> AsyncIterator[bytes]:
    yield b"ID3" + b"\x00" * 1000


async def test_every_answer_of_the_fake_and_every_request_of_the_client_keep_the_contract(
    spotdown: FakeSpotdown,
) -> None:
    client = SpotdownClient(frozenset({"upload", "youtube"}))
    spotdown.add_analysis(None, "youtube:vid00000000")  # a cached candidate with its summary
    titles = ["Song", *(f"Song [{s}]" for s in MATCH_STATUSES)]
    tracks = tuple(TrackQuery(f"t{n}", t, ("Artist",), 200.0) for n, t in enumerate(titles))
    await client.match(tracks, SCOPE_A)
    uploaded = await client.upload(chunks(), SCOPE_A, idempotency_key("u"))
    assert isinstance(uploaded, Uploaded)
    for n, end in enumerate(ENDS):
        started = await client.start_analysis(
            SCOPE_A,
            f"ref-{end}",
            idempotency_key(end),
            source_ref=f"youtube:src{n}",
            reference=Reference("spotify", "4uLU6hMCjMI75M1A2tKUQC", 200.0),
        )
        assert isinstance(started, Pending)
        for _ in range(3):
            await client.poll(started.job_id)
    await client.start_analysis(
        SCOPE_A, "ref-up", idempotency_key("a"), upload_id=uploaded.upload_id
    )
    await client.lookup_source("youtube:vid00000000")
    analysis_id = next(iter(spotdown.analyses))
    await client.get_analysis(analysis_id)

    answers = answers_of(spotdown)
    kinds = {kind for kind, _ in answers}
    assert kinds == {"match-v1", "job-v1", "lookup-v1", "analysis-v1"}
    for kind, value in answers:
        assert errors(kind, value) == [], (kind, value)
    outcomes = {
        (v["status"], v.get("outcome"), v.get("failure_kind")) for k, v in answers if k == "job-v1"
    }
    assert len(outcomes) == len(ENDS) + 2  # every end, plus queued and running
    statuses = {v["status"] for k, v in answers if k == "match-v1"}
    assert statuses == {"matched", *MATCH_STATUSES}

    for seen in spotdown.seen:
        if seen.path == "/v2/match":
            assert Draft202012Validator(MATCH_REQUEST).is_valid(json.loads(seen.body))
        if seen.path == "/v2/analyses":
            assert Draft202012Validator(ANALYSIS_REQUEST).is_valid(json.loads(seen.body))


def answers_of(fake: FakeSpotdown) -> list[tuple[str, dict[str, Any]]]:
    """Each ``200`` JSON answer of the fake, with the schema its route must keep."""
    answers: list[tuple[str, dict[str, Any]]] = []
    for path, status, content in fake.answered:
        if status != 200:
            continue
        body = json.loads(content)
        if path == "/v2/match":
            answers += [("match-v1", r) for r in body["results"]]
        elif path.startswith("/v2/jobs/"):
            answers.append(("job-v1", body))
        elif path == "/v2/analyses/lookup":
            answers.append(("lookup-v1", body))
        elif path.startswith("/v2/analyses/") and path != "/v2/analyses":
            answers.append(("analysis-v1", body))
    return answers


def _broken_matches() -> list[dict[str, Any]]:
    good = example("match-v1.matched.json")
    cases: list[dict[str, Any]] = []
    for change in (
        lambda v: v.update(status="not_found"),  # best without matched/uncertain
        lambda v: v.update(retry_after_s=3),  # retry_after_s without rate_limited
        lambda v: v["best"].update(band="confident", recording_match_confidence=0.5),
        lambda v: v["best"].update(duration_check="maybe"),
        lambda v: v.update(extra=1),
        lambda v: v.update(schema_version="match-v2"),
        lambda v: v["alternates"].extend([v["best"], v["best"]]),  # 3 alternates
        lambda v: v["best"].pop("cached_analysis_id"),  # a summary without the id
        lambda v: v.pop("matcher_version"),
    ):
        broken = copy.deepcopy(good)
        change(broken)
        cases.append(broken)
    return cases


@pytest.mark.parametrize("broken", _broken_matches())
def test_the_match_parser_refuses_what_the_schema_refuses(broken: dict[str, Any]) -> None:
    assert errors("match-v1", broken) != []
    assert parse_match_result(broken) is None


def _broken_jobs() -> list[dict[str, Any]]:
    done = example("job-v1.done.json")
    failed = example("job-v1.failed.json")
    cases: list[dict[str, Any]] = []
    for base, change in (
        (done, lambda v: v.pop("analysis_id")),
        (done, lambda v: v.update(outcome="not_found")),
        (failed, lambda v: v.update(analysis_id="an_1")),
        (failed, lambda v: v.pop("failure_kind")),
        (failed, lambda v: v.update(outcome="rate_limited")),  # without retry_after_s
        (done, lambda v: v.update(retry_after_s=1)),
        (done, lambda v: v.update(status="running")),  # running with an analysis id
        (done, lambda v: v.update(status="paused")),
    ):
        broken = copy.deepcopy(base)
        change(broken)
        cases.append(broken)
    return cases


@pytest.mark.parametrize("broken", _broken_jobs())
def test_the_job_parser_refuses_what_the_schema_refuses(broken: dict[str, Any]) -> None:
    assert errors("job-v1", broken) != []
    assert parse_job(raw(broken)) is None


@pytest.mark.parametrize(
    "broken",
    [
        {"schema_version": "lookup-v1", "analysis_id": ""},
        {"schema_version": "lookup-v1"},
        {"schema_version": "lookup-v2", "analysis_id": "an_1"},
        {"schema_version": "lookup-v1", "analysis_id": "an_1", "x": 1},
    ],
)
def test_the_lookup_parser_refuses_what_the_schema_refuses(broken: dict[str, Any]) -> None:
    assert errors("lookup-v1", broken) != []
    assert parse_lookup(raw(broken)) is None


@pytest.mark.parametrize("group", ["provenance", "signal", "tempo", "grid", "structure", "events"])
def test_the_analysis_parser_refuses_a_document_without_a_required_group(group: str) -> None:
    broken = example("analysis-v1.minimal.json")
    broken.pop(group)
    assert errors("analysis-v1", broken) != []
    assert parse_analysis(raw(broken)) is None
