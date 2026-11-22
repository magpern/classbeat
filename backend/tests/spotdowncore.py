"""The fake spotdown (contract v2, ADR 0040; SD-P4, #1301): the core, with no pytest and no
``classbeat`` import, so the e2e stack can mount this one file (``e2e/fake-spotdown``, #1337) and
the backend tests run the same code in process (``spotdownfake``: the fixture over an httpx
transport: no socket, no network, deterministic). Public test values only.

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

``fake.end`` sets how every new job ends (for a caller whose ``client_ref`` it does not choose);
``fake.ends_by_source[source]`` how the next jobs of a ``youtube:<id>`` source end, one each;
``fake.documents[source]`` the document of that source's analyses; ``fake.references[source]``
records the alignment reference last asked for (written only: no test reads it), and
``fake.aligned[analysis_id]`` the reference an analysis is aligned to: as in contract v2, a cache hit asked with another reference than the hit's
answers with an analysis aligned to the one asked (a new id), never with another's alignment. An
analysis of a ``youtube`` source needs no ``cache_scope`` (contract v2: a global source).
A document of an analysis held for ``sha256:<hex>`` carries that sha256 in its provenance.

``fake.inject[path] = [httpx.Response, ...]`` answers the next requests to ``path`` with those
instead (a ``503``, a ``429`` with ``Retry-After``, a redirect, an oversized or gzip body);
``fake.connect_error`` raises ``httpx.ConnectError``. ``fake.seen`` records every request and
``fake.answered`` every answer of the fake's own routes (for the contract test).
"""

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# The vendored contract's examples; the e2e container mounts them elsewhere (the variable).
CONTRACTS = Path(
    os.environ.get("FAKE_SPOTDOWN_CONTRACTS")
    or Path(__file__).resolve().parent / "contracts" / "spotdown"
)
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


_EXPLICIT_WEIGHTS = {("caller", True): 0.9, ("caller", False): 0.8,
                     ("caller_match", True): 0.9, ("caller_match", False): 0.8}  # fmt: skip


def evidence_of(request: dict[str, Any], match_flag: bool | None) -> list[dict[str, Any]]:
    """The metadata evidence of a new analysis: the request's ``explicit`` (``caller``) and the
    flag of the match that chose the video (``caller_match``), as spotdown#36 lists them."""
    found: list[dict[str, Any]] = []
    if isinstance(request.get("explicit"), bool):
        found.append({"source": "caller", "value": request["explicit"]})
    if match_flag is not None:
        found.append({"source": "caller_match", "value": match_flag})
    return found


def explicit_group(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """``content.explicit`` from the evidence, by spotdown's rule: ``unknown`` without any, ``yes``
    on any true flag (at most 0.5 against a false one), else ``no``."""
    yes = [_EXPLICIT_WEIGHTS[(e["source"], True)] for e in evidence if e["value"] is True]
    no = [_EXPLICIT_WEIGHTS[(e["source"], False)] for e in evidence if e["value"] is False]
    value, confidence = ("yes", max(yes)) if yes else ("no", max(no)) if no else ("unknown", 0.0)
    if yes and no:
        confidence = min(confidence, 0.5)
    return {"value": value, "confidence": confidence, "method": "metadata", "evidence": evidence}


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": code}})


@dataclass
class FakeSpotdown:
    key: str = SERVICE_KEY
    seen: list[Seen] = field(default_factory=lambda: [])
    answered: list[tuple[str, int, bytes]] = field(default_factory=lambda: [])  # path, status, body
    inject: dict[str, list[httpx.Response]] = field(default_factory=lambda: {})
    connect_error: bool = False
    end: str | None = None  # every new job ends so (one of ``ENDS``), whatever its client_ref
    # per source (``youtube:<id>``): how its next jobs end, one per job (before ``end``)
    ends_by_source: dict[str, list[str]] = field(default_factory=lambda: {})
    # per source: the analysis-v1 document of its analyses (else the minimal example)
    documents: dict[str, dict[str, Any]] = field(default_factory=lambda: {})
    references: dict[str, dict[str, Any]] = field(default_factory=lambda: {})  # the last asked
    # per analysis id: the reference it is aligned to (contract v2: an aligned analysis per
    # reference, so a cache hit with another reference never answers with another's alignment)
    aligned: dict[str, dict[str, Any]] = field(default_factory=lambda: {})
    # spotdown#36: the caller's ``explicit`` flag of the latest match that chose a video (a later
    # match without a flag removes it), and the evidence each new analysis was made with.
    match_flags: dict[str, bool] = field(default_factory=lambda: {})  # youtube:<id>: flag
    explicit_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {})
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
        """The minimal example with this id; one held for ``sha256:<hex>`` names those bytes."""
        by = self.analyses.get(analysis_id, (None, ""))[1]
        base = self.documents.get(by)
        document = copy.deepcopy(base if base is not None else example("analysis-v1.minimal.json"))
        document["analysis_id"] = analysis_id
        if by.startswith("sha256:"):
            document["provenance"]["audio"]["sha256"] = by.removeprefix("sha256:")
        asked = self.aligned.get(analysis_id)  # a placeholder becomes the asked one (#1354)
        alignment = document.get("alignment")
        if asked and isinstance(alignment, dict) and alignment["reference"]["id"] == "0" * 32:
            alignment["reference"] = dict(asked)
            document["provenance"]["alignment"]["reference"] = dict(asked)
        if analysis_id in self.explicit_evidence and "content" not in document:
            document["content"] = {"explicit": explicit_group(self.explicit_evidence[analysis_id])}
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
            asked = json.loads(body)
            scope = str(asked.get("cache_scope", ""))
            global_ok = "cache_scope" not in asked and str(asked.get("source_ref", "")).startswith(
                "youtube:"
            )  # contract v2: optional for a youtube-only request (a global source)
            return self._idempotent(request, scope, body, self._start, global_ok=global_ok)
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

    def _idempotent(
        self,
        request: httpx.Request,
        scope: str,
        body: bytes,
        route: Any,
        *,
        global_ok: bool = False,
    ) -> Any:
        key = request.headers.get("idempotency-key")
        if not key:
            return _error(422, "idempotency_key_required")
        if not scope and not global_ok:
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

    def video(self, kind: str, n: int, track: dict[str, Any]) -> str:
        """The video id a match gives the track at position ``n`` of its batch: ``vid`` (the best)
        or ``alt`` (the alternate) and the position. The e2e fake overrides it, to give a song the
        same video in every batch and to make that video's analysis document (#1337)."""
        return f"{kind}{n:08d}"

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
                best = f"youtube:{self.video('vid', n, track)}"
                result["best"] = self._candidate(scope, best, confidence)
                self._keep_flag(best, track.get("explicit"))
                alternate = f"youtube:{self.video('alt', n, track)}"
                result["alternates"] = [self._candidate(scope, alternate, 0.65)]
                result["matcher_version"] = "1"
            if status == "rate_limited":
                result["retry_after_s"] = 30
            results.append(result)
        return httpx.Response(200, json={"results": results})

    def _keep_flag(self, source: str, flag: object) -> None:
        if isinstance(flag, bool):
            self.match_flags[source] = flag
        else:
            self.match_flags.pop(source, None)

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
        cached = self._find(scope or None, by)
        job_id = self._next("job")
        if cached is not None:
            if "reference" in request:
                cached = self._aligned_hit(cached, by, dict(request["reference"]))
            return httpx.Response(
                200, json={"job_id": job_id, "status": "done", "analysis_id": cached}
            )
        ref = str(request["client_ref"])
        ends = self.ends_by_source.get(by)
        if ends:
            end = ends.pop(0)
        else:
            end = self.end or next((e for e in ENDS if ref.endswith(e)), "done")
        if "reference" in request:
            self.references[by] = dict(request["reference"])
        self.jobs[job_id] = _Job(job_id, scope, ref, end)
        if end in ("done", "alignment_failed"):
            held_scope = None if by.startswith("youtube:") else scope
            made = self.add_analysis(held_scope, by)
            if "reference" in request:
                self.aligned[made] = dict(request["reference"])
            self.jobs[job_id].analysis_id = made
            self.explicit_evidence[made] = evidence_of(request, self.match_flags.get(by))
        return httpx.Response(202, json={"job_id": job_id, "status": "queued"})

    def _aligned_hit(self, cached: str, by: str, reference: dict[str, Any]) -> str:
        """The analysis a cache hit with ``reference`` answers (contract v2, "aligned analysis":
        the alignment cache is keyed on the audio and the reference): the one already aligned to
        it, else the hit itself when it has no alignment yet, else a new analysis of the same
        audio in the hit's scope, aligned to it."""
        held = self.analyses[cached][0]
        for analysis_id, (scope, key) in self.analyses.items():
            if key == by and scope == held and self.aligned.get(analysis_id) == reference:
                return analysis_id
        if cached not in self.aligned:
            self.aligned[cached] = reference
            return cached
        made = self.add_analysis(held, by)
        self.aligned[made] = reference
        if cached in self.explicit_evidence:
            self.explicit_evidence[made] = self.explicit_evidence[cached]
        return made

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
        self.aligned = {k: v for k, v in self.aligned.items() if k in self.analyses}
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
