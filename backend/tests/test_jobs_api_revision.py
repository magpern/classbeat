"""``GET /api/jobs/{id}/revision`` (U1, #93): the draft summary's one read. Through the real app
on the app role under RLS, with the test-only kind. No provider is contacted."""

import json
import uuid
from typing import Any
from uuid import UUID

import pytest

from jobapi import UNKNOWN, Browser
from jobcore import JobsEnv

pytestmark = pytest.mark.usefixtures("jenv")

DRAFT = {
    "name": "Tuesday climb",
    "description": "Two hills.",
    "segments": [
        {
            "name": "Warm up",
            "kind": "warmup",
            "intervals": [
                {"sec": 60, "zone": 2, "rpm": [80, 90], "standing": False, "cue": "Easy now"},
                {"sec": 30, "zone": 5, "rpm": "beat", "standing": True},
            ],
        }
    ],
}
PLANTED = "planted-link-id-and-fingerprint"


def plant(jenv: JobsEnv, job_id: str, content: dict[str, Any], *, number: int = 1) -> UUID:
    row = jenv.row(UUID(job_id))
    revision = uuid.uuid4()
    music = {
        "source": "mowl_spotify_playlist",
        "link_id": str(uuid.uuid4()),
        "fingerprint": "a" * 64,
        "unknown_bpm": [{"index": 3, "title": "Song without BPM"}],
        "planted": PLANTED,
    }
    jenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, job_id) VALUES (:id, :o, :c, :n, 'draft', "
        "CAST(:content AS jsonb), :h, CAST(:music AS jsonb), CAST(:prov AS jsonb), :j)",
        id=revision, o=row.owner_id, c=row.class_id, n=number, content=json.dumps(content),
        h=b"\x01" * 32, music=json.dumps(music), prov=json.dumps({"model": PLANTED}),
        j=UUID(job_id),
    )  # fmt: skip
    return revision


async def test_the_owner_reads_only_the_allowed_keys(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id = (await alice.create()).json()["id"]
    revision = plant(jenv, job_id, DRAFT)
    response = await alice.get(f"/api/jobs/{job_id}/revision")
    assert response.status_code == 200, response.text
    shown = response.json()
    assert set(shown) == {
        "revision_id", "number", "kind", "content", "unknown_bpm", "created_at"
    }  # fmt: skip
    assert (shown["revision_id"], shown["number"], shown["kind"]) == (str(revision), 1, "draft")
    assert shown["content"]["name"] == "Tuesday climb"
    assert shown["content"]["segments"][0]["intervals"][1]["rpm"] == "beat"
    assert shown["unknown_bpm"] == [{"index": 3, "title": "Song without BPM"}]
    assert PLANTED not in response.text  # no provenance, link id or fingerprint


async def test_the_newest_draft_of_the_job_is_shown(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id = (await alice.create()).json()["id"]
    plant(jenv, job_id, DRAFT, number=1)
    newer = plant(jenv, job_id, DRAFT | {"name": "Second"}, number=2)
    shown = (await alice.get(f"/api/jobs/{job_id}/revision")).json()
    assert (shown["revision_id"], shown["number"]) == (str(newer), 2)


async def test_no_revision_yet_is_409(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id = (await alice.create()).json()["id"]
    response = await alice.get(f"/api/jobs/{job_id}/revision")
    assert (response.status_code, response.json()) == (409, {"detail": {"reason": "no_revision"}})


async def test_foreign_unknown_and_malformed_ids_are_the_same_404(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    bob: Browser = await browsers()
    bobs_job = (await bob.create()).json()["id"]
    plant(jenv, bobs_job, DRAFT)
    answers = [
        await alice.get(f"/api/jobs/{bobs_job}/revision"),
        await alice.get(f"/api/jobs/{UNKNOWN}/revision"),
        await alice.get("/api/jobs/not-a-uuid/revision"),
    ]
    assert {(a.status_code, a.text) for a in answers} == {
        (404, json.dumps({"detail": {"reason": "not_found"}}, separators=(",", ":")))
    }
    assert (await bob.get(f"/api/jobs/{bobs_job}/revision")).status_code == 200


async def test_stored_content_outside_the_draft_schema_is_not_echoed(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    job_id = (await alice.create()).json()["id"]
    plant(jenv, job_id, DRAFT | {"secret_field": PLANTED})  # the schema forbids extra fields
    response = await alice.get(f"/api/jobs/{job_id}/revision")
    assert (response.status_code, PLANTED in response.text) == (500, False)


async def test_a_signed_out_request_is_401(env: Any, browsers: Any) -> None:
    await browsers()
    response = await env.client.get(f"/api/jobs/{UNKNOWN}/revision")
    assert response.status_code == 401
