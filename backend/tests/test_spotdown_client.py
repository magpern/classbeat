"""``classbeat.spotdown.client`` against the in-process fake spotdown (``spotdownfake``): every
per-track status of ``/v2/match``, every job outcome, uploads, lookups, the scope delete, the
failure taxonomy of ADR 0040 section 8, and "off means nothing is sent". No network (netguard)."""

import gzip
import json
from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest

from classbeat.spotdown import opener
from classbeat.spotdown.answer_model import Uploaded
from classbeat.spotdown.client import Deleted, SpotdownClient
from classbeat.spotdown.failure_policy import (
    RETRY_AFTER_MAX,
    AnalysisReady,
    Matched,
    Pending,
    TrackFailure,
    UncertainMatch,
    Unreachable,
)
from classbeat.spotdown.request_model import Reference, RequestRefused, TrackQuery, analysis_body
from classbeat.spotdown.scope_calc import idempotency_key
from spotdownfake import ENDS, SCOPE_A, SCOPE_B, SERVICE_KEY, FakeSpotdown, spotdown

__all__ = ["spotdown"]  # the fixture

ALL = frozenset({"upload", "youtube"})
YT = "youtube:abcdefghijk"
AUDIO = b"ID3" + bytes(range(256)) * 64


def track(ref: str, title: str = "Song") -> TrackQuery:
    return TrackQuery(ref, title, ("Artist",), 213.0, "USRC17607839")


async def chunks(data: bytes = AUDIO, size: int = 1000) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def finish(client: SpotdownClient, job_id: str) -> object:
    outcome: object = Pending(job_id, "queued")
    for _ in range(5):
        outcome = await client.poll(job_id)
        if not isinstance(outcome, Pending):
            break
    return outcome


# --- off: nothing is sent ------------------------------------------------------------------


async def test_with_nothing_installed_every_call_is_not_configured_and_nothing_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never(*_: object) -> httpx.AsyncClient:
        raise AssertionError("a client was built while spotdown is off")

    monkeypatch.setattr(opener, "open_client", never)
    monkeypatch.setattr(opener, "open_upload_client", never)
    client = SpotdownClient(ALL)
    off = Unreachable("not_configured")
    key = idempotency_key("job", "1")
    assert await client.match((track("t1"),)) == off
    assert await client.upload(chunks(), SCOPE_A, key) == off
    assert await client.start_analysis(SCOPE_A, "t1", key, source_ref="youtube:abcdefghijk") == off
    assert await client.poll("job_1") == off
    assert await client.lookup_source("youtube:abcdefghijk") == off
    assert await client.lookup_content("0" * 64, SCOPE_A) == off
    assert await client.get_analysis("an_1") == off
    assert await client.delete_scope(SCOPE_A) == off


@pytest.mark.parametrize("sources", [frozenset[str](), frozenset({"upload"})])
async def test_youtube_calls_need_the_youtube_source(
    spotdown: FakeSpotdown, sources: frozenset[str]
) -> None:
    client = SpotdownClient(sources)
    key = idempotency_key("job", "1")
    assert await client.match((track("t1"),)) == Unreachable("source_off")
    started = await client.start_analysis(SCOPE_A, "t1", key, source_ref="youtube:abcdefghijk")
    assert started == Unreachable("source_off")
    assert await client.lookup_source("youtube:abcdefghijk") == Unreachable("source_off")
    assert spotdown.seen == []


async def test_upload_calls_need_the_upload_source(spotdown: FakeSpotdown) -> None:
    client = SpotdownClient(frozenset({"youtube"}))
    key = idempotency_key("job", "1")
    assert await client.upload(chunks(), SCOPE_A, key) == Unreachable("source_off")
    assert await client.start_analysis(SCOPE_A, "t1", key, upload_id="up_1") == Unreachable(
        "source_off"
    )
    assert await client.lookup_content("0" * 64, SCOPE_A) == Unreachable("source_off")
    assert spotdown.seen == []


# --- match ---------------------------------------------------------------------------------


async def test_match_maps_every_per_track_status(spotdown: FakeSpotdown) -> None:
    cached = spotdown.add_analysis(None, "youtube:vid00000000")
    tracks = (
        track("t0"),
        track("t1", "Song [not_found]"),
        track("t2", "Song [uncertain]"),
        track("t3", "Song [source_unavailable]"),
        track("t4", "Song [provider_error]"),
        track("t5", "Song [rate_limited]"),
    )
    results = await SpotdownClient(ALL).match(tracks, SCOPE_A, idempotency_key("m", "1"))
    assert isinstance(results, tuple)
    matched, *rest = results
    assert isinstance(matched, Matched)
    assert matched.best.source == "youtube:vid00000000"
    assert matched.best.cached_analysis_id == cached
    assert matched.best.cached_summary is not None
    assert matched.best.cached_summary.bpm == 128.0
    assert [a.band for a in matched.alternates] == ["uncertain"]
    assert isinstance(rest[1], UncertainMatch)
    assert rest[1].best.band == "uncertain"
    assert [r for i, r in enumerate(rest) if i != 1] == [
        TrackFailure("not_found"),
        TrackFailure("source_unavailable"),
        TrackFailure("provider_error"),
        TrackFailure("rate_limited", retry_after_s=30.0),
    ]
    (seen,) = spotdown.seen
    assert (seen.method, seen.path, seen.query) == ("POST", "/v2/match", "")
    assert seen.headers["idempotency-key"] == idempotency_key("m", "1")


async def test_a_match_answer_out_of_order_or_short_is_a_bad_answer(
    spotdown: FakeSpotdown,
) -> None:
    good = httpx.Response(200, json={"results": []})
    spotdown.inject["/v2/match"] = [good]
    assert await SpotdownClient(ALL).match((track("t1"),)) == Unreachable("bad_answer")


@pytest.mark.parametrize(
    "bad",
    [
        TrackQuery("", "Song", ("A",), 1.0),
        TrackQuery("t", "", ("A",), 1.0),
        TrackQuery("t", "Song", (), 1.0),
        TrackQuery("t", "Song", ("A",), float("nan")),
        TrackQuery("t", "Song", ("A",), -1.0),
        TrackQuery("t", "Song", ("A",), 1.0, "not an isrc"),
    ],
)
async def test_a_bad_track_is_refused_before_anything_is_sent(
    spotdown: FakeSpotdown, bad: TrackQuery
) -> None:
    with pytest.raises(RequestRefused):
        await SpotdownClient(ALL).match((bad,))
    assert spotdown.seen == []


# --- upload, analyses, jobs ----------------------------------------------------------------


async def test_upload_then_analyse_then_poll_to_done_then_read(spotdown: FakeSpotdown) -> None:
    client = SpotdownClient(ALL)
    uploaded = await client.upload(chunks(), SCOPE_A, idempotency_key("job", "up"))
    assert isinstance(uploaded, Uploaded)
    assert uploaded.size == len(AUDIO)
    started = await client.start_analysis(
        SCOPE_A,
        "track-7",
        idempotency_key("job", "an"),
        upload_id=uploaded.upload_id,
        reference=Reference("spotify", "4uLU6hMCjMI75M1A2tKUQC", 213.0),
    )
    assert isinstance(started, Pending)
    assert await client.poll(started.job_id) == Pending(started.job_id, "queued")
    assert await client.poll(started.job_id) == Pending(started.job_id, "running")
    ready = await client.poll(started.job_id)
    assert isinstance(ready, AnalysisReady)
    document = await client.get_analysis(ready.analysis_id, SCOPE_A)
    assert isinstance(document, dict)
    assert document["analysis_id"] == ready.analysis_id
    assert spotdown.seen[-1].query == f"cache_scope={SCOPE_A}"
    put = spotdown.seen[0]
    assert (put.method, put.path, put.query) == ("PUT", "/v2/uploads", f"cache_scope={SCOPE_A}")
    assert put.headers["content-type"] == "application/octet-stream"
    assert put.body == AUDIO
    # Unchanged audio is found by content in the scope, never in another one.
    sha = uploaded.sha256
    assert await client.lookup_content(sha, SCOPE_A) == AnalysisReady(ready.analysis_id)
    assert await client.lookup_content(sha, SCOPE_B) is None


@pytest.mark.parametrize("end", ENDS)
async def test_every_job_outcome_is_typed(spotdown: FakeSpotdown, end: str) -> None:
    client = SpotdownClient(ALL)
    started = await client.start_analysis(
        SCOPE_A, f"track-{end}", idempotency_key("job", end), source_ref="youtube:abcdefghijk"
    )
    assert isinstance(started, Pending)
    outcome = await finish(client, started.job_id)
    if end == "done":
        assert isinstance(outcome, AnalysisReady)
        return
    assert isinstance(outcome, TrackFailure)
    status, _, kind = end.partition(":")
    assert outcome.status == status
    if status == "alignment_failed":
        assert outcome.analysis_id is not None  # the file-clock analysis exists
        assert outcome.failure_kind == "alignment_failed"
    elif status == "processing_failed":
        assert outcome.failure_kind == kind
        assert outcome.analysis_id is None
    elif status == "rate_limited":
        assert outcome.retry_after_s == 45.0
    else:
        assert outcome == TrackFailure(status)  # pyright: ignore[reportArgumentType]


async def test_a_cached_analysis_is_ready_at_once(spotdown: FakeSpotdown) -> None:
    analysis_id = spotdown.add_analysis(None, "youtube:cached00001")
    client = SpotdownClient(ALL)
    got = await client.start_analysis(
        SCOPE_B, "t1", idempotency_key("j", "1"), source_ref="youtube:cached00001"
    )
    assert got == AnalysisReady(analysis_id)
    assert await client.lookup_source("youtube:cached00001") == AnalysisReady(analysis_id)


async def test_a_private_analysis_is_read_only_with_the_scope_that_holds_it(
    spotdown: FakeSpotdown,
) -> None:
    mine = spotdown.add_analysis(SCOPE_A, "sha256:" + "b" * 64)
    client = SpotdownClient(ALL)
    got = await client.get_analysis(mine, SCOPE_A)
    assert isinstance(got, dict)
    assert got["analysis_id"] == mine
    assert await client.get_analysis(mine) == Unreachable("rejected")  # no scope: global only
    assert await client.get_analysis(mine, SCOPE_B) == Unreachable("rejected")
    assert [seen.query for seen in spotdown.seen] == [
        f"cache_scope={SCOPE_A}",
        "",
        f"cache_scope={SCOPE_B}",
    ]


async def test_a_global_analysis_is_read_with_or_without_a_scope(spotdown: FakeSpotdown) -> None:
    shared = spotdown.add_analysis(None, "youtube:shared00001")
    client = SpotdownClient(ALL)
    for scope in (None, SCOPE_A, SCOPE_B):
        got = await client.get_analysis(shared, scope)
        assert isinstance(got, dict)
        assert got["analysis_id"] == shared


@pytest.mark.parametrize("scope", ["", "a b", "a" * 129, "scope/../x"])
async def test_a_bad_analysis_scope_never_reaches_the_query(
    spotdown: FakeSpotdown, scope: str
) -> None:
    with pytest.raises(RequestRefused):
        await SpotdownClient(ALL).get_analysis("an_1", scope)
    assert spotdown.seen == []


@pytest.mark.parametrize(
    "source_ref",
    [
        "youtube:abc",  # not 11 characters
        "youtube:abcdefghijkl",
        "youtube:abcdefghij/",
        "url:https://example.com/a.mp3",
        "upload:up_1",
        "spotify:4uLU6hMCjMI75M1A2tKUQC",
        "YOUTUBE:abcdefghijk",
        "youtube:abcdefghijk\n",
        "sha256:" + "a" * 64,
    ],
)
async def test_only_a_youtube_video_id_is_a_source_ref(
    spotdown: FakeSpotdown, source_ref: str
) -> None:
    client = SpotdownClient(ALL)
    with pytest.raises(RequestRefused):
        await client.start_analysis(SCOPE_A, "t1", idempotency_key("j", "s"), source_ref=source_ref)
    with pytest.raises(RequestRefused):
        await client.lookup_source(source_ref, SCOPE_A)
    assert spotdown.seen == []


def test_an_analysis_body_without_a_scope_only_for_a_global_source() -> None:
    """SD-P6 stage B (#1303): ``cache_scope`` may be left out only for a ``source_ref`` (a
    ``youtube`` source is global); an upload is private and always carries its owner's scope."""
    with pytest.raises(RequestRefused):
        analysis_body(None, "t1", upload_id="up_1")
    with pytest.raises(RequestRefused):
        analysis_body(None, "t1", upload_id="up_1", reference=Reference("spotify", "r", 200.0))
    assert "cache_scope" not in json.loads(analysis_body(None, "t1", source_ref=YT))
    assert json.loads(analysis_body(SCOPE_A, "t1", source_ref=YT))["cache_scope"] == SCOPE_A
    assert json.loads(analysis_body(SCOPE_A, "t1", upload_id="up_1"))["cache_scope"] == SCOPE_A


async def test_an_upload_analysis_without_its_scope_is_refused_before_anything_is_sent(
    spotdown: FakeSpotdown,
) -> None:
    with pytest.raises(RequestRefused):
        await SpotdownClient(ALL).start_analysis(
            None, "t1", idempotency_key("j", "u"), upload_id="up_1"
        )
    assert spotdown.seen == []


async def test_a_youtube_analysis_sends_its_scope_when_given_and_none_when_not(
    spotdown: FakeSpotdown,
) -> None:
    client = SpotdownClient(ALL)
    with_scope = await client.start_analysis(
        SCOPE_A, "t1", idempotency_key("j", "a"), source_ref=YT
    )
    other = "youtube:zyxwvutsrqp"
    without = await client.start_analysis(None, "t2", idempotency_key("j", "b"), source_ref=other)
    assert isinstance(with_scope, Pending)
    assert isinstance(without, Pending)
    first, second = (json.loads(s.body) for s in spotdown.seen if s.path == "/v2/analyses")
    assert first["cache_scope"] == SCOPE_A
    assert "cache_scope" not in second


async def test_an_upload_and_a_sha256_lookup_never_leave_without_a_scope(
    spotdown: FakeSpotdown,
) -> None:
    """#1338: even a shared (studio) analysis carries a scope; there is no scope-less upload."""

    async def audio() -> AsyncIterator[bytes]:
        yield b"x"

    client, none = SpotdownClient(ALL), cast(str, None)
    with pytest.raises(RequestRefused):
        await client.upload(audio(), none, idempotency_key("j", "u"))
    with pytest.raises(RequestRefused):
        await client.lookup_content("0" * 64, none)
    assert spotdown.seen == []


def test_the_fake_still_needs_a_scope_for_an_upload_analysis(spotdown: FakeSpotdown) -> None:
    """The fake's scope-less form is for a ``youtube`` source only, as the contract says."""
    body = json.dumps({"client_ref": "t1", "upload_id": "up_1"}).encode()
    request = httpx.Request(
        "POST", "http://spotdown-api:8000/v2/analyses", content=body,
        headers={"Authorization": f"Bearer {SERVICE_KEY}", "Idempotency-Key": "k" * 64},
    )  # fmt: skip
    answer = spotdown.handle(request, body)
    assert answer.status_code == 422
    assert answer.json()["error"]["code"] == "scope_required"


@pytest.mark.parametrize("duration", [0.0, -1.0, 7200.5, float("nan"), float("inf")])
async def test_a_reference_duration_outside_the_contract_is_refused(
    spotdown: FakeSpotdown, duration: float
) -> None:
    reference = Reference("spotify", "4uLU6hMCjMI75M1A2tKUQC", duration)
    with pytest.raises(RequestRefused):
        await SpotdownClient(ALL).start_analysis(
            SCOPE_A, "t1", idempotency_key("j", "r"), source_ref=YT, reference=reference
        )
    assert spotdown.seen == []


async def test_the_same_idempotency_key_returns_the_first_answer_and_another_body_is_a_conflict(
    spotdown: FakeSpotdown,
) -> None:
    client = SpotdownClient(ALL)
    key = idempotency_key("job", "start")
    first = await client.start_analysis(SCOPE_A, "t1", key, source_ref="youtube:abcdefghijk")
    again = await client.start_analysis(SCOPE_A, "t1", key, source_ref="youtube:abcdefghijk")
    assert first == again
    other = await client.start_analysis(SCOPE_A, "t1", key, source_ref="youtube:xyzxyzxyzxy")
    assert other == Unreachable("conflict")


async def test_delete_scope_removes_the_scope_only(spotdown: FakeSpotdown) -> None:
    mine = spotdown.add_analysis(SCOPE_A, "sha256:" + "a" * 64)
    theirs = spotdown.add_analysis(SCOPE_B, "sha256:" + "a" * 64)
    client = SpotdownClient(ALL)
    assert await client.delete_scope(SCOPE_A) == Deleted()
    assert await client.delete_scope(SCOPE_A) == Deleted()  # idempotent
    assert await client.lookup_content("a" * 64, SCOPE_A) is None
    assert await client.lookup_content("a" * 64, SCOPE_B) == AnalysisReady(theirs)
    assert mine not in spotdown.analyses


# --- the failure taxonomy over HTTP --------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (httpx.Response(503), Unreachable("server_error")),
        (httpx.Response(500, text="boom"), Unreachable("server_error")),
        (httpx.Response(401), Unreachable("unauthorized")),
        (httpx.Response(403), Unreachable("unauthorized")),
        (httpx.Response(404), Unreachable("rejected")),
        (httpx.Response(413), Unreachable("too_large")),
        (
            httpx.Response(422, json={"error": {"code": "source_disabled", "message": "x"}}),
            Unreachable("source_disabled"),
        ),
        (
            httpx.Response(422, json={"error": {"code": "other", "message": "x"}}),
            Unreachable("rejected"),
        ),
        (httpx.Response(429, headers={"Retry-After": "12"}), TrackFailure("rate_limited", 12.0)),
        (httpx.Response(429), TrackFailure("rate_limited", 60.0)),
        (
            httpx.Response(429, headers={"Retry-After": "99999"}),
            TrackFailure("rate_limited", RETRY_AFTER_MAX),
        ),
        (httpx.Response(200, text="not json"), Unreachable("bad_answer")),
        (httpx.Response(200, json={"schema_version": "job-v2"}), Unreachable("bad_answer")),
        (
            httpx.Response(200, content=gzip.compress(b"{}"), headers={"content-encoding": "gzip"}),
            Unreachable("too_large"),
        ),
        (httpx.Response(302, headers={"location": "/v2/jobs/job_1"}), Unreachable("blocked")),
        (httpx.Response(200, content=b"x" * (16 * 1024 + 1)), Unreachable("too_large")),
    ],
)
async def test_a_poll_maps_every_http_failure(
    spotdown: FakeSpotdown, answer: httpx.Response, expected: object
) -> None:
    spotdown.inject["/v2/jobs/job_1"] = [answer]
    assert await SpotdownClient(ALL).poll("job_1") == expected


async def test_a_job_answer_for_another_job_is_a_bad_answer(spotdown: FakeSpotdown) -> None:
    view = {"schema_version": "job-v1", "job_id": "job_2", "status": "queued", "outcome": None}
    spotdown.inject["/v2/jobs/job_1"] = [httpx.Response(200, json=view)]
    assert await SpotdownClient(ALL).poll("job_1") == Unreachable("bad_answer")


async def test_an_analysis_with_another_id_is_a_bad_answer(spotdown: FakeSpotdown) -> None:
    real = spotdown.add_analysis(None, "youtube:xxxxxxxxxxx")
    spotdown.inject["/v2/analyses/an_wanted"] = [httpx.Response(200, json=spotdown.analysis(real))]
    assert await SpotdownClient(ALL).get_analysis("an_wanted") == Unreachable("bad_answer")


async def test_a_connection_error_is_unreachable(spotdown: FakeSpotdown) -> None:
    spotdown.connect_error = True
    assert await SpotdownClient(ALL).delete_scope(SCOPE_A) == Unreachable("connection")


async def test_a_wrong_key_is_unauthorized(spotdown: FakeSpotdown) -> None:
    spotdown.key = "another-key-that-spotdown-expects-0123456789"
    assert await SpotdownClient(ALL).lookup_source("youtube:abcdefghijk") == Unreachable(
        "unauthorized"
    )


async def test_an_upload_over_the_cap_is_cut_off(
    spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classbeat import egress_spotdown

    monkeypatch.setattr(egress_spotdown, "UPLOAD_MAX", len(AUDIO) - 1)
    got = await SpotdownClient(ALL).upload(chunks(), SCOPE_A, idempotency_key("j", "u"))
    assert got == Unreachable("too_large")
    assert spotdown.uploads == {}


@pytest.mark.parametrize(
    "path_value", ["../admin", "job/1", "job?x=1", "", "a" * 65, "job 1", "jöb"]
)
async def test_an_id_that_is_not_the_contracts_never_reaches_a_path(
    spotdown: FakeSpotdown, path_value: str
) -> None:
    client = SpotdownClient(ALL)
    with pytest.raises(RequestRefused):
        await client.poll(path_value)
    with pytest.raises(RequestRefused):
        await client.get_analysis(path_value)
    assert spotdown.seen == []


async def test_the_key_goes_only_into_the_authorization_header(spotdown: FakeSpotdown) -> None:
    client = SpotdownClient(ALL)
    await client.match((track("t1"),), SCOPE_A)
    await client.lookup_source("youtube:abcdefghijk", SCOPE_A)
    await client.upload(chunks(), SCOPE_A, idempotency_key("j", "u"))
    for seen in spotdown.seen:
        assert seen.headers["authorization"] == f"Bearer {SERVICE_KEY}"
        others = {k: v for k, v in seen.headers.items() if k != "authorization"}
        assert SERVICE_KEY not in repr(others) + seen.path + seen.query
        assert SERVICE_KEY.encode() not in seen.body
        assert "user-agent" not in seen.headers
        assert seen.headers["accept-encoding"] == "identity"
