"""The e2e fake spotdown (``e2e/fake-spotdown/server.py``, #1337) on a loopback socket, the handler
the stack serves: every document it serves validates against the vendored ``analysis-v1`` schema (a
standing check: the e2e stack runs on these documents); an upload has a fixed analysis per sha256
whose length is the audio's own; and a body over the cap is refused with ``413``, never cut short
(a cut body would give another sha256). No network: loopback only."""

import copy
import hashlib
import importlib.util
import io
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import numpy
import pytest
import soundfile
from jsonschema import Draft202012Validator

from spotdowncore import CONTRACTS, SCOPE_A, SCOPE_B

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "e2e" / "fake-spotdown" / "server.py"
KEY_FILE = ROOT / "e2e" / "fake-spotdown" / "service-key.txt"
VALIDATOR = Draft202012Validator(json.loads((CONTRACTS / "analysis-v1.json").read_text("utf-8")))

pytestmark = pytest.mark.skipif(not SERVER.is_file(), reason="not a full checkout")


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[httpx.Client, ModuleType]]:
    spec = importlib.util.spec_from_file_location("e2e_fake_spotdown_server", SERVER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "KEY_FILE", str(KEY_FILE))
    httpd = module.ThreadingHTTPServer(("127.0.0.1", 0), module.make_handler(module.State()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    headers = {"Authorization": f"Bearer {KEY_FILE.read_text('utf-8').strip()}"}
    client = httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_address[1]}", headers=headers)
    try:
        yield client, module
    finally:
        client.close()
        httpd.shutdown()
        httpd.server_close()


def finished(client: httpx.Client, started: httpx.Response, scope: str = SCOPE_A) -> dict[str, Any]:
    """The analysis document of a started job (done at once on a cache hit, else polled)."""
    job = started.json()
    for _ in range(5):
        if job["status"] == "done":
            break
        job = client.get(f"/v2/jobs/{job['job_id']}").json()
    assert job["status"] == "done", job
    got = client.get(f"/v2/analyses/{job['analysis_id']}", params={"cache_scope": scope})
    assert got.status_code == 200
    return got.json()


def youtube_documents(client: httpx.Client, titles: list[str]) -> list[dict[str, Any]]:
    tracks = [
        {"client_ref": str(n), "title": t, "artists": ["Band"], "duration_s": 240}
        for n, t in enumerate(titles)
    ]
    results = client.post("/v2/match", json={"tracks": tracks}).json()["results"]
    found: list[dict[str, Any]] = []
    for n, result in enumerate(results):
        for candidate in (result["best"], *result["alternates"]):
            start = client.post(
                "/v2/analyses",
                json={"source_ref": candidate["source"], "client_ref": "x"},
                headers={"Idempotency-Key": f"k{n}-{candidate['source']}"},
            )
            found.append(finished(client, start))
    return found


def silence(seconds: int) -> bytes:
    out = io.BytesIO()
    soundfile.write(out, numpy.zeros(8000 * seconds), 8000, format="WAV", subtype="PCM_16")
    return out.getvalue()


def uploaded(client: httpx.Client, body: bytes, scope: str, key: str) -> dict[str, Any]:
    put = client.put(
        "/v2/uploads", params={"cache_scope": scope}, content=body, headers={"Idempotency-Key": key}
    )
    assert put.status_code == 201, put.text
    assert put.json()["sha256"] == hashlib.sha256(body).hexdigest()  # whole, never cut
    start = client.post(
        "/v2/analyses",
        json={"upload_id": put.json()["upload_id"], "cache_scope": scope, "client_ref": "x"},
        headers={"Idempotency-Key": f"a-{key}"},
    )
    return finished(client, start, scope)


def test_every_youtube_document_validates_against_analysis_v1(
    server: tuple[httpx.Client, ModuleType],
) -> None:
    client, _ = server
    titles = [f"Some Song {n}" for n in (0, 1, 7, 24, 59, 150)] + ["No number here"]
    documents = youtube_documents(client, titles)
    assert len(documents) == 2 * len(titles)  # the best and the alternate of each
    for document in documents:
        assert list(VALIDATOR.iter_errors(document)) == []
    assert {d["tempo"]["bpm"] for d in documents} > {100.0 + 3 * 24}  # a tempo by catalogue number


def test_the_schema_check_can_fail(server: tuple[httpx.Client, ModuleType]) -> None:
    client, _ = server
    [document, *_] = youtube_documents(client, ["Some Song 3"])
    assert list(VALIDATOR.iter_errors(document)) == []
    broken = copy.deepcopy(document)
    broken["tempo"]["bpm"] = "fast"
    assert list(VALIDATOR.iter_errors(broken)) != []
    del broken["tempo"]
    assert list(VALIDATOR.iter_errors(broken)) != []


def test_an_upload_has_one_fixed_analysis_per_sha256_of_its_own_length(
    server: tuple[httpx.Client, ModuleType],
) -> None:
    client, _ = server
    short, long = silence(100), silence(130)
    first = uploaded(client, short, SCOPE_A, "u1")
    again = uploaded(client, short, SCOPE_B, "u2")  # the same bytes in another scope
    other = uploaded(client, long, SCOPE_A, "u3")
    for document in (first, again, other):
        assert list(VALIDATOR.iter_errors(document)) == []
    assert first["provenance"]["audio"]["sha256"] == hashlib.sha256(short).hexdigest()
    assert first["signal"]["duration_s"] == again["signal"]["duration_s"] == 100.0
    assert other["signal"]["duration_s"] == 130.0
    assert first["tempo"] == again["tempo"]  # a function of the sha256 only
    assert first["grid"] == again["grid"]
    assert other["provenance"]["audio"]["sha256"] != first["provenance"]["audio"]["sha256"]


def test_a_body_over_the_cap_is_refused_with_413_not_cut_short(
    server: tuple[httpx.Client, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, module = server
    monkeypatch.setattr(module, "UPLOAD_MAX", 300_000)
    big = b"\0" * (module.BODY_MAX + 1)
    assert client.post("/v2/match", content=big).status_code == 413
    assert client.put("/v2/uploads", params={"cache_scope": SCOPE_A}, content=b"\0" * 300_001,
                      headers={"Idempotency-Key": "k"}).status_code == 413  # fmt: skip
    # An upload larger than the request cap, below its own cap, is read whole (its digest is right).
    whole = b"\1" * 250_000
    put = client.put(
        "/v2/uploads",
        params={"cache_scope": SCOPE_A},
        content=whole,
        headers={"Idempotency-Key": "w"},
    )
    assert put.status_code == 201
    assert put.json() == {
        "upload_id": put.json()["upload_id"],
        "sha256": hashlib.sha256(whole).hexdigest(),
        "bytes": 250_000,
    }
    state = client.get("/__e2e/state").json()["requests"]
    assert [r["method"] for r in state].count("PUT") == 1  # the refused ones never reached the core


def test_the_fake_needs_the_service_key(server: tuple[httpx.Client, ModuleType]) -> None:
    client, _ = server
    bare = httpx.Client(base_url=str(client.base_url))
    assert bare.post("/v2/match", json={"tracks": []}).status_code == 401


def _aligned(client: httpx.Client, source: str, reference: dict[str, Any], key: str) -> str:
    start = client.post(
        "/v2/analyses",
        json={"source_ref": source, "client_ref": "p0", "reference": reference},
        headers={"Idempotency-Key": key},
    )
    return finished(client, start)["analysis_id"]


def test_a_cache_hit_with_another_reference_is_aligned_to_the_one_asked(
    server: tuple[httpx.Client, ModuleType],
) -> None:
    """The e2e stack's MOWL playlist ("Invented Song 3", a 240 s track) and a song pick's catalogue
    track 3 share one video (the title's number), so its global analysis is a cache hit for the
    second caller; as in contract v2 that caller gets an analysis aligned to its own reference,
    never to the first caller's (stage D refused that one: every e2e pick after a MOWL playlist
    design ended ``analysis_failed``)."""
    client, _ = server
    [result] = client.post(
        "/v2/match",
        json={"tracks": [{"client_ref": "0", "title": "Invented Song 3", "artists": ["A"],
                          "duration_s": 240}]},
    ).json()["results"]  # fmt: skip
    source = result["best"]["source"]
    mowl = {"kind": "spotify", "id": "a" * 32, "duration_s": 240.0}
    pick = {"kind": "spotify", "id": "b" * 32, "duration_s": 213.4}
    first = _aligned(client, source, mowl, "k1")
    second = _aligned(client, source, pick, "k2")
    assert second != first
    for analysis_id, reference in ((first, mowl), (second, pick)):
        got = client.get(f"/v2/analyses/{analysis_id}").json()
        assert got["alignment"]["reference"] == reference
        assert got["provenance"]["alignment"]["reference"] == reference
        assert list(VALIDATOR.iter_errors(got)) == []
    assert _aligned(client, source, pick, "k3") == second  # the same reference: the same analysis
    assert _aligned(client, source, mowl, "k4") == first
