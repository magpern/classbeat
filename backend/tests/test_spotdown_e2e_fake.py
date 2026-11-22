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

import spotifycore
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


def reference_of(client: httpx.Client, title: str, artist: str, reference_id: str) -> str:
    """The id of the alignment reference in the document of the best video of one song, analysed
    asking for ``reference_id`` (stage B of a pick, or of a design on a playlist)."""
    track = {"client_ref": "0", "title": title, "artists": [artist], "duration_s": 240}
    best = client.post("/v2/match", json={"tracks": [track]}).json()["results"][0]["best"]
    start = client.post(
        "/v2/analyses",
        json={
            "source_ref": best["source"],
            "client_ref": "x",
            "reference": {"kind": "spotify_track", "id": reference_id, "duration_s": 240.0},
        },
        headers={"Idempotency-Key": f"k-{reference_id}"},
    )
    return str(finished(client, start)["alignment"]["reference"]["id"])


def test_two_songs_whose_titles_end_in_the_same_number_are_never_one_video(
    server: tuple[httpx.Client, ModuleType],
) -> None:
    """The fake MOWL's playlist songs ("Invented Song 3") and the fake Spotify's catalogue ("Tempo
    Cascade 3") share their catalogue numbers. A design on the playlist analyses one video per
    number for ever; a pick of a catalogue song must not be handed that analysis (aligned to the
    other song: phase 2's ``verified`` ends ``analysis_failed``, #1415)."""
    client, _ = server
    assert reference_of(client, "Invented Song 3", "Made-up Artist 0", "a" * 32) == "a" * 32
    assert reference_of(client, "Tempo Cascade 3", "Some Artist", "b" * 32) == "b" * 32
    # the same song again is the same video: its analysis is reused, aligned to the same track
    assert reference_of(client, "Tempo Cascade 3", "Some Artist", "b" * 32) == "b" * 32


def test_every_song_of_both_fake_worlds_has_its_own_video(
    server: tuple[httpx.Client, ModuleType],
) -> None:
    """The fake MOWL's eight playlist songs and the fake Spotify's catalogue, together: no two
    songs share a video, and a song keeps its video in every batch (a revise asks again)."""
    client, _ = server
    songs = [(f"Invented Song {i}", "Made-up Artist") for i in range(8)]
    songs += [(str(t["name"]), str(t["artist"])) for t in spotifycore._catalogue().values()]
    tracks = [
        {"client_ref": str(n), "title": title, "artists": [artist], "duration_s": 240}
        for n, (title, artist) in enumerate(songs)
    ]
    first = client.post("/v2/match", json={"tracks": tracks}).json()["results"]
    again = client.post("/v2/match", json={"tracks": tracks[::-1]}).json()["results"]
    best = [r["best"]["source"] for r in first]
    alternate = [r["alternates"][0]["source"] for r in first]
    assert len(set(best)) == len(set(alternate)) == len(songs)
    assert set(best).isdisjoint(alternate)
    assert [r["best"]["source"] for r in again[::-1]] == best


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
