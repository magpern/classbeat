"""The in-process fake spotdown (contract v2, ADR 0040; SD-P4, #1301) over an httpx transport: no
socket, no network, deterministic. Public test values only.

**What it answers** (the vendored contract, ``tests/contracts/spotdown``):

- ``POST /v2/match``: one ``match-v1`` per track, in order. A track's status comes from a token in
  its title, ``[<status>]`` (``[not_found]``, ``[uncertain]``, ``[source_unavailable]``,
  ``[provider_error]``, ``[rate_limited]``); without one it is ``matched``. A candidate whose
  source has an analysis in the caller's scope (or a global ``youtube`` one) carries
  ``cached_analysis_id`` and ``cached_summary``.
- ``PUT /v2/uploads`` (needs ``Idempotency-Key`` and ``cache_scope``) keeps the bytes per scope.
- ``POST /v2/analyses`` (needs ``Idempotency-Key``): a cache hit (the same sha256 or source in the
  scope, a ``youtube`` source in any scope) is ``200`` done at once; otherwise ``202`` queued, and
  each ``GET /v2/jobs/{id}`` moves the job one step (queued, running, then its end). The end
  comes from a token in ``client_ref``: ``done`` (the default), ``alignment_failed``,
  ``processing_failed:<failure kind>``, ``not_found``, ``uncertain``, ``source_unavailable``,
  ``provider_error`` or ``rate_limited``. A used upload is gone (``422``).
- ``GET /v2/analyses/{id}``: an ``analysis-v1`` document (the vendored minimal example with this
  id) for an analysis in the ``cache_scope`` query's scope or a global one, else ``404``;
  ``GET /v2/analyses/lookup`` by ``source`` or ``sha256``, in the scope, else ``404``;
  ``DELETE /v2/scopes/{scope}``: everything of the scope, ``204`` either way.
- Every ``/v2`` request without ``Authorization: Bearer <SERVICE_KEY>`` is ``401``; the same
  ``Idempotency-Key`` with another body is ``409``. Errors use ``{"error": {"code", "message"}}``.

``fake.inject[path] = [httpx.Response, ...]`` answers the next requests to ``path`` with those
instead (a ``503``, a ``429`` with ``Retry-After``, a redirect, an oversized or gzip body);
``fake.connect_error`` raises ``httpx.ConnectError``. ``fake.seen`` records every request and
``fake.answered`` every answer of the fake's own routes (for the contract test).
"""

import copy
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from classbeat import egress_spotdown
from classbeat.spotdown import opener

CONTRACTS = Path(__file__).resolve().parent / "contracts" / "spotdown"
ORIGIN = "http://spotdown-api:8000"
SERVICE_KEY = "fake-spotdown-service-key-Hq7Lm2Zx9Pw4Rt6Yv8Nb3Kc"  # searched for in outputs
SCOPE_A = "o1-scopeAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
SCOPE_B = "o1-scopeBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
MATCH_STATUSES = ("not_found", "uncertain", "source_unavailable", "provider_error", "rate_limited")
ENDS = (
    "done",
    "alignment_failed",
    "not_found",
    "uncertain",
    "source_unavailable",
    "provider_error",
    "rate_limited",
    "processing_failed:decode_error",
    "processing_failed:timeout",
    "processing_failed:out_of_memory",
    "processing_failed:analysis_error",
)


def example(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / "examples" / name).read_text("utf-8"))


@dataclass
class Seen:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes


@dataclass
class _Job:
    job_id: str
    scope: str
    client_ref: str
    end: str
    polls: int = 0
    analysis_id: str | None = None


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": code}})


@dataclass
class FakeSpotdown:
    key: str = SERVICE_KEY
    seen: list[Seen] = field(default_factory=lambda: [])
    answered: list[tuple[str, int, bytes]] = field(default_factory=lambda: [])  # path, status, body
    inject: dict[str, list[httpx.Response]] = field(default_factory=lambda: {})
    connect_error: bool = False
    uploads: dict[str, tuple[str, str]] = field(default_factory=lambda: {})  # id: scope, sha
    analyses: dict[str, tuple[str | None, str]] = field(default_factory=lambda: {})  # id: scope, by
    jobs: dict[str, _Job] = field(default_factory=lambda: {})
    idempotency: dict[tuple[str, str, str], tuple[str, httpx.Response]] = field(
        default_factory=lambda: {}
    )
    _serial: int = 0

    def _next(self, prefix: str) -> str:
        self._serial += 1
        return f"{prefix}_{self._serial:04d}"

    # --- the state a test sets up or reads -------------------------------------------------

    def add_analysis(self, scope: str | None, by: str) -> str:
        """An analysis held for ``by`` (``sha256:<hex>`` or ``youtube:<id>``); ``None`` = global."""
        analysis_id = self._next("an")
        self.analyses[analysis_id] = (scope, by)
        return analysis_id

    def _find(self, scope: str | None, by: str) -> str | None:
        for analysis_id, (held, key) in self.analyses.items():
            if key == by and (held is None or held == scope):
                return analysis_id
        return None

    def analysis(self, analysis_id: str) -> dict[str, Any]:
        document = copy.deepcopy(example("analysis-v1.minimal.json"))
        document["analysis_id"] = analysis_id
        return document

    # --- routes -----------------------------------------------------------------------------

    def handle(self, request: httpx.Request, body: bytes) -> httpx.Response:
        path = request.url.path
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"code": "unauthorized", "message": ""}})
        query = dict(request.url.params)
        if request.method == "POST" and path == "/v2/match":
            return self._match(body)
        if request.method == "PUT" and path == "/v2/uploads":
            return self._idempotent(request, query.get("cache_scope", ""), body, self._upload)
        if request.method == "POST" and path == "/v2/analyses":
            scope = str(json.loads(body).get("cache_scope", ""))
            return self._idempotent(request, scope, body, self._start)
        if request.method == "GET" and path == "/v2/analyses/lookup":
            return self._lookup(query)
        if request.method == "GET" and path.startswith("/v2/analyses/"):
            return self._get_analysis(path.removeprefix("/v2/analyses/"), query.get("cache_scope"))
        if request.method == "GET" and path.startswith("/v2/jobs/"):
            return self._poll(path.removeprefix("/v2/jobs/"))
        if request.method == "DELETE" and path.startswith("/v2/scopes/"):
            return self._delete(path.removeprefix("/v2/scopes/"))
        return _error(404, "not_found")

    def _get_analysis(self, analysis_id: str, scope: str | None) -> httpx.Response:
        """An analysis held in the caller's scope or a global one; any other id is ``404``
        (without ``cache_scope`` only a global analysis is readable)."""
        held = self.analyses.get(analysis_id)
        if held is None or (held[0] is not None and held[0] != scope):
            return _error(404, "not_found")
        return httpx.Response(200, json=self.analysis(analysis_id))

    def _idempotent(self, request: httpx.Request, scope: str, body: bytes, route: Any) -> Any:
        key = request.headers.get("idempotency-key")
        if not key:
            return _error(422, "idempotency_key_required")
        if not scope:
            return _error(422, "scope_required")
        digest = hashlib.sha256(body).hexdigest()
        recorded = self.idempotency.get((scope, request.url.path, key))
        if recorded is not None:
            if recorded[0] != digest:
                return _error(409, "idempotency_conflict")
            return recorded[1]
        response = route(scope, body)
        self.idempotency[(scope, request.url.path, key)] = (digest, response)
        return response

    def _candidate(self, scope: str | None, source: str, confidence: float) -> dict[str, Any]:
        band = "confident" if confidence >= 0.85 else "uncertain"
        item: dict[str, Any] = {
            "source": source,
            "recording_match_confidence": confidence,
            "band": band,
            "duration_s": 213.8,
            "duration_delta_s": 0.8,
            "duration_check": "ok",
        }
        cached = self._find(scope, source)
        if cached is not None:
            item["cached_analysis_id"] = cached
            item["cached_summary"] = {
                "analyzer_version": "1.0.0",
                "bpm": 128.0,
                "bpm_confidence": 0.9,
                "duration_s": 213.8,
                "has_beats": True,
            }
        return item

    def _match(self, body: bytes) -> httpx.Response:
        request = json.loads(body)
        scope = request.get("cache_scope")
        results: list[dict[str, Any]] = []
        for n, track in enumerate(request["tracks"]):
            title = str(track["title"])
            status = next((s for s in MATCH_STATUSES if f"[{s}]" in title), "matched")
            result: dict[str, Any] = {
                "schema_version": "match-v1",
                "client_ref": track["client_ref"],
                "status": status,
                "alternates": [],
            }
            if status in ("matched", "uncertain"):
                confidence = 0.92 if status == "matched" else 0.7
                result["best"] = self._candidate(scope, f"youtube:vid{n:08d}", confidence)
                result["alternates"] = [self._candidate(scope, f"youtube:alt{n:08d}", 0.65)]
                result["matcher_version"] = "1"
            if status == "rate_limited":
                result["retry_after_s"] = 30
            results.append(result)
        return httpx.Response(200, json={"results": results})

    def _upload(self, scope: str, body: bytes) -> httpx.Response:
        upload_id = self._next("up")
        digest = hashlib.sha256(body).hexdigest()
        self.uploads[upload_id] = (scope, digest)
        return httpx.Response(
            201, json={"upload_id": upload_id, "sha256": digest, "bytes": len(body)}
        )

    def _start(self, scope: str, body: bytes) -> httpx.Response:
        request = json.loads(body)
        if "upload_id" in request:
            held = self.uploads.pop(request["upload_id"], None)
            if held is None or held[0] != scope:
                return _error(422, "upload_unknown")
            by = f"sha256:{held[1]}"
        else:
            by = str(request["source_ref"])
        cached = self._find(scope, by)
        job_id = self._next("job")
        if cached is not None:
            return httpx.Response(
                200, json={"job_id": job_id, "status": "done", "analysis_id": cached}
            )
        ref = str(request["client_ref"])
        end = next((e for e in ENDS if ref.endswith(e)), "done")
        self.jobs[job_id] = _Job(job_id, scope, ref, end)
        if end in ("done", "alignment_failed"):
            held_scope = None if by.startswith("youtube:") else scope
            self.jobs[job_id].analysis_id = self.add_analysis(held_scope, by)
        return httpx.Response(202, json={"job_id": job_id, "status": "queued"})

    def _poll(self, job_id: str) -> httpx.Response:
        job = self.jobs.get(job_id)
        if job is None:
            return _error(404, "not_found")
        job.polls += 1
        view: dict[str, Any] = {
            "schema_version": "job-v1",
            "job_id": job.job_id,
            "client_ref": job.client_ref,
            "status": "queued",
            "outcome": None,
        }
        if job.polls == 2:
            view["status"] = "running"
        elif job.polls > 2:
            view.update(self._end(job))
        return httpx.Response(200, json=view)

    @staticmethod
    def _end(job: _Job) -> dict[str, Any]:
        if job.end in ("done", "alignment_failed"):
            end: dict[str, Any] = {"status": "done", "analysis_id": job.analysis_id}
            if job.end == "alignment_failed":
                end |= {"outcome": "alignment_failed", "failure_kind": "alignment_failed"}
            return end
        outcome, _, kind = job.end.partition(":")
        end = {"status": "failed", "outcome": outcome}
        if kind:
            end["failure_kind"] = kind
        if outcome == "rate_limited":
            end["retry_after_s"] = 45
        return end

    def _lookup(self, query: dict[str, str]) -> httpx.Response:
        scope = query.get("cache_scope")
        if "sha256" in query:
            if not scope:
                return _error(422, "scope_required")
            found = self._find(scope, f"sha256:{query['sha256']}")
        else:
            found = self._find(scope, query.get("source", ""))
        if found is None:
            return _error(404, "not_found")
        return httpx.Response(200, json={"schema_version": "lookup-v1", "analysis_id": found})

    def _delete(self, scope: str) -> httpx.Response:
        self.analyses = {k: v for k, v in self.analyses.items() if v[0] != scope}
        self.uploads = {k: v for k, v in self.uploads.items() if v[0] != scope}
        self.jobs = {k: v for k, v in self.jobs.items() if v.scope != scope}
        self.idempotency = {k: v for k, v in self.idempotency.items() if k[0] != scope}
        return httpx.Response(204)


def transport(fake: FakeSpotdown) -> httpx.MockTransport:
    """The fake behind an httpx transport (the request recorded first, injections next)."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        headers = {k.lower(): v for k, v in request.headers.items()}
        fake.seen.append(
            Seen(request.method, request.url.path, request.url.query.decode(), headers, body)
        )
        if fake.connect_error:
            raise httpx.ConnectError("injected: no connection", request=request)
        queued = fake.inject.get(request.url.path)
        if queued:
            return queued.pop(0)
        response = fake.handle(request, body)
        fake.answered.append((request.url.path, response.status_code, response.content))
        return response

    return httpx.MockTransport(handler)


@pytest.fixture
def spotdown(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSpotdown]:
    """The fake behind ``classbeat.spotdown.opener`` (the production builder, through
    ``spotdown_test_client``, over the fake transport), with spotdown installed for the test."""
    fake = FakeSpotdown()
    key = SecretStr(SERVICE_KEY)

    def open_client(total_seconds: float) -> httpx.AsyncClient:
        return egress_spotdown.spotdown_test_client(
            ORIGIN, key, transport(fake), total_seconds=total_seconds
        )

    def open_upload_client() -> httpx.AsyncClient:
        return egress_spotdown.spotdown_test_client(ORIGIN, key, transport(fake), upload=True)

    egress_spotdown.install_spotdown(ORIGIN, key)
    monkeypatch.setattr(opener, "open_client", open_client)
    monkeypatch.setattr(opener, "open_upload_client", open_upload_client)
    try:
        yield fake
    finally:
        egress_spotdown.install_spotdown(None, None)
