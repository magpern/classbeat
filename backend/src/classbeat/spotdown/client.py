"""The spotdown calls of contract v2 (ADR 0040 sections 4 and 7): exactly ``match``, ``upload``,
``start_analysis``, ``poll``, ``lookup_source``, ``lookup_content``, ``get_analysis`` and
``delete_scope``, nothing else. Each opens one client through ``opener`` (so only
``classbeat.egress_spotdown``'s builder, which pins the one origin and adds the service key) and
returns a typed result of ``failure_policy``; **no call raises** for anything spotdown does.

- **Off means nothing is sent.** With no origin and key installed (``CLASSBEAT_SPOTDOWN_URL``
  empty, the default) every call returns ``Unreachable("not_configured")`` before a client is
  built. A source not in ``CLASSBEAT_SPOTDOWN_SOURCES`` is ``Unreachable("source_off")``, also
  before anything is built: ``match`` and a ``youtube:`` source need ``youtube``, an upload and an
  analysis of an upload need ``upload``.
- **Headers:** ``Accept: application/json``, ``Accept-Encoding: identity``, ``Content-Type`` for a
  body and ``Idempotency-Key`` (required on ``upload`` and ``start_analysis``, optional on
  ``match``). The service key is set by the builder's transport, never here.
- **Answers** are read raw, identity encoding only, each under its own cap (``*_ANSWER_MAX``;
  ``swedebeat_gateway.answer.read_capped``), and parsed strictly (``answer_model``). A ``429`` is
  ``rate_limited`` with its ``Retry-After``; any other unexpected status, a refused request, a
  connection error, the deadline or an answer the contract does not allow is ``Unreachable``.
- **Nothing secret leaves in a result or a log line**: nothing here logs, and no exception is
  kept (httpx's errors hold the request, whose headers hold the key).
"""

from collections.abc import AsyncIterable
from dataclasses import dataclass
from functools import partial
from typing import Final

from classbeat import egress_spotdown
from classbeat.egress_spotdown import ANALYSIS_SECONDS, CALL_SECONDS, MATCH_SECONDS
from classbeat.spotdown import opener
from classbeat.spotdown import request_model as rq
from classbeat.spotdown.answer_model import (
    Uploaded,
    parse_analysis,
    parse_job,
    parse_lookup,
    parse_started,
    parse_uploaded,
)
from classbeat.spotdown.exchange import JSON, Answer, Failure, exchange, failure, headers_for
from classbeat.spotdown.failure_policy import (
    AnalysisReady,
    JobOutcome,
    MatchOutcome,
    Pending,
    Unreachable,
    job_outcome,
    match_outcome,
)
from classbeat.spotdown.match_model import parse_match_answer

MATCH_ANSWER_MAX: Final = 1024 * 1024
JOB_ANSWER_MAX: Final = 16 * 1024
SMALL_ANSWER_MAX: Final = 4 * 1024  # a lookup, an upload's or a start's answer
ANALYSIS_ANSWER_MAX: Final = egress_spotdown.ANSWER_MAX


@dataclass(frozen=True, slots=True)
class Deleted:
    """``DELETE /v2/scopes/{scope}`` answered ``204`` (idempotent)."""


class SpotdownClient:
    """The calls, for the sources this deployment enabled (``sources_policy.parse_sources``)."""

    def __init__(self, sources: frozenset[str]) -> None:
        self._sources = sources

    def _refusal(self, source: str | None) -> Unreachable | None:
        if not egress_spotdown.spotdown_installed():
            return Unreachable("not_configured")
        if source is not None and source not in self._sources:
            return Unreachable("source_off")
        return None

    async def match(
        self,
        tracks: tuple[rq.TrackQuery, ...],
        cache_scope: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[MatchOutcome, ...] | Failure:
        """``POST /v2/match``: one outcome per track, in order. No download, no analysis."""
        content = rq.match_body(tracks, cache_scope)
        refused = self._refusal("youtube")
        if refused is not None:
            return refused
        headers = headers_for(body=JSON, idempotency_key=idempotency_key)
        answer = await exchange(
            partial(opener.open_client, MATCH_SECONDS),
            "POST",
            "/v2/match",
            success=frozenset({200}),
            cap=MATCH_ANSWER_MAX,
            headers=headers,
            content=content,
        )
        if isinstance(answer, Unreachable):
            return answer
        if answer.status != 200:
            return failure(answer)
        results = parse_match_answer(answer.body, tuple(t.client_ref for t in tracks))
        if results is None:
            return Unreachable("bad_answer")
        return tuple(match_outcome(r) for r in results)

    async def upload(
        self, chunks: AsyncIterable[bytes], cache_scope: str, idempotency_key: str
    ) -> Uploaded | Failure:
        """``PUT /v2/uploads``: the bytes streamed (``UPLOAD_MAX``, ``UPLOAD_SECONDS``). Callers
        try ``lookup_content`` first, so unchanged audio is never uploaded twice."""
        params = {"cache_scope": rq.check_scope(cache_scope)}
        headers = headers_for(body="application/octet-stream", idempotency_key=idempotency_key)
        refused = self._refusal("upload")
        if refused is not None:
            return refused
        answer = await exchange(
            opener.open_upload_client,
            "PUT",
            "/v2/uploads",
            success=frozenset({201}),
            cap=SMALL_ANSWER_MAX,
            headers=headers,
            params=params,
            content=chunks,
        )
        if isinstance(answer, Unreachable):
            return answer
        if answer.status != 201:
            return failure(answer)
        return parse_uploaded(answer.body) or Unreachable("bad_answer")

    async def start_analysis(
        self,
        cache_scope: str | None,
        client_ref: str,
        idempotency_key: str,
        *,
        upload_id: str | None = None,
        source_ref: str | None = None,
        reference: rq.Reference | None = None,
        explicit: bool | None = None,
    ) -> AnalysisReady | Pending | Failure:
        """``POST /v2/analyses``: ready at once on a cache hit (``200``), else queued (``202``).
        ``cache_scope`` may be ``None`` only for a ``source_ref`` (a global ``youtube`` source).
        ``explicit``: the caller's explicit-lyrics flag, sent only when known."""
        content = rq.analysis_body(
            cache_scope,
            client_ref,
            upload_id=upload_id,
            source_ref=source_ref,
            reference=reference,
            explicit=explicit,
        )
        source = "upload" if source_ref is None else source_ref.split(":", 1)[0]
        refused = self._refusal(source)
        if refused is not None:
            return refused
        answer = await exchange(
            partial(opener.open_client, ANALYSIS_SECONDS),
            "POST",
            "/v2/analyses",
            success=frozenset({200, 202}),
            cap=SMALL_ANSWER_MAX,
            headers=headers_for(body=JSON, idempotency_key=idempotency_key),
            content=content,
        )
        if isinstance(answer, Unreachable):
            return answer
        if answer.status not in (200, 202):
            return failure(answer)
        started = parse_started(answer.body, cached=answer.status == 200)
        if started is None:
            return Unreachable("bad_answer")
        if started.analysis_id is not None:
            return AnalysisReady(started.analysis_id)
        return Pending(started.job_id, "queued")

    async def poll(self, job_id: str) -> JobOutcome | Unreachable:
        """``GET /v2/jobs/{id}``: pending, ready or a per-track status (callers back off)."""
        path = rq.job_path(job_id)
        refused = self._refusal(None)
        if refused is not None:
            return refused
        answer = await _get(path, JOB_ANSWER_MAX)
        if isinstance(answer, Unreachable):
            return answer
        if answer.status != 200:
            return failure(answer)
        job = parse_job(answer.body)
        if job is None or job.job_id != job_id:
            return Unreachable("bad_answer")
        return job_outcome(job)

    async def lookup_source(
        self, source_ref: str, cache_scope: str | None = None
    ) -> AnalysisReady | Failure | None:
        """``GET /v2/analyses/lookup?source=``: the cached analysis, or ``None`` on a ``404``."""
        params = rq.source_lookup_params(source_ref, cache_scope)
        refused = self._refusal(source_ref.split(":", 1)[0])
        return refused or await _lookup(params)

    async def lookup_content(self, sha256: str, cache_scope: str) -> AnalysisReady | Failure | None:
        """``GET /v2/analyses/lookup?sha256=``, in the caller's scope, before any upload."""
        params = rq.content_lookup_params(sha256, cache_scope)
        refused = self._refusal("upload")
        return refused or await _lookup(params)

    async def get_analysis(
        self, analysis_id: str, cache_scope: str | None = None
    ) -> dict[str, object] | Failure:
        """``GET /v2/analyses/{id}``: the ``analysis-v1`` document (the same id). Without
        ``cache_scope`` spotdown reads only a global (``youtube``) analysis; a private one needs
        the scope that holds it."""
        path, params = rq.analysis_path(analysis_id), rq.analysis_params(cache_scope)
        refused = self._refusal(None)
        if refused is not None:
            return refused
        answer = await _get(path, ANALYSIS_ANSWER_MAX, seconds=ANALYSIS_SECONDS, params=params)
        if isinstance(answer, Unreachable):
            return answer
        if answer.status != 200:
            return failure(answer)
        document = parse_analysis(answer.body)
        if document is None or document["analysis_id"] != analysis_id:
            return Unreachable("bad_answer")
        return document

    async def delete_scope(self, cache_scope: str) -> Deleted | Failure:
        """``DELETE /v2/scopes/{scope}``: everything held for the scope (account deletion)."""
        path = rq.scope_path(cache_scope)
        refused = self._refusal(None)
        if refused is not None:
            return refused
        answer = await exchange(
            partial(opener.open_client, CALL_SECONDS),
            "DELETE",
            path,
            success=frozenset({204}),
            cap=SMALL_ANSWER_MAX,
            headers=headers_for(body=None, idempotency_key=None),
        )
        if isinstance(answer, Unreachable):
            return answer
        return Deleted() if answer.status == 204 else failure(answer)


async def _get(
    path: str, cap: int, *, seconds: float = CALL_SECONDS, params: dict[str, str] | None = None
) -> Answer | Unreachable:
    return await exchange(
        partial(opener.open_client, seconds),
        "GET",
        path,
        success=frozenset({200}),
        cap=cap,
        headers=headers_for(body=None, idempotency_key=None),
        params=params,
    )


async def _lookup(params: dict[str, str]) -> AnalysisReady | Failure | None:
    answer = await _get("/v2/analyses/lookup", SMALL_ANSWER_MAX, params=params)
    if isinstance(answer, Unreachable):
        return answer
    if answer.status == 404:
        return None
    if answer.status != 200:
        return failure(answer)
    analysis_id = parse_lookup(answer.body)
    return Unreachable("bad_answer") if analysis_id is None else AnalysisReady(analysis_id)
