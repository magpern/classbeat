"""``design.pool_sync`` (#1535): the pool's sync wait, and what a timeout logs (counts, no ids).

Pure fakes: MOWL's track read is a stand-in function and the clock is a counter; no network.
"""

import logging
from typing import Any

import pytest
from mowl import api as mowl_api

from classbeat.design import pool_sync
from classbeat.jobs import JobFailure

WANTED = ["SnapId0000000000000001", "SnapId0000000000000002", "SnapId0000000000000003"]


def listing(ids: list[str]) -> list[dict[str, Any]]:
    return [{"SpotifyTrackID": i, "Title": f"Song {n}"} for n, i in enumerate(ids)]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def test_counts_are_expected_listed_and_overlap_as_often() -> None:
    assert pool_sync.counts(WANTED, [WANTED[0], WANTED[0], "Other00000000000000001"]) == {
        "expected": 3,
        "listed": 3,
        "overlap": 1,
    }
    assert pool_sync.counts(WANTED, None) == {"expected": 3, "listed": 0, "overlap": 0}


def test_a_matching_list_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[int] = []

    def tracks(_creds: Any, playlist_id: int) -> list[dict[str, Any]]:
        reads.append(playlist_id)
        return listing(list(reversed(WANTED)))  # MOWL's order is not relied on

    monkeypatch.setattr(mowl_api, "playlist_tracks", tracks)
    pool_sync.wait_synced(object(), 42, WANTED, 5.0, lambda: None, Clock())  # pyright: ignore[reportArgumentType]
    assert reads == [42]


def test_a_timeout_logs_counts_only_and_ends_pool_not_synced(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stale = [WANTED[0], "Old0000000000000000001"]
    monkeypatch.setattr(mowl_api, "playlist_tracks", lambda _c, _p: listing(stale))  # pyright: ignore[reportUnknownLambdaType]
    monkeypatch.setattr(pool_sync, "SYNC_POLL_SECONDS", 0.0)
    level = caplog.at_level(logging.WARNING, logger=pool_sync.__name__)
    with level, pytest.raises(pool_sync.Halt) as stop:
        pool_sync.wait_synced(object(), 42, WANTED, 2.0, lambda: None, Clock(), "job-1")  # pyright: ignore[reportArgumentType]
    assert isinstance(stop.value.cause, JobFailure)
    assert stop.value.cause.category == "pool_not_synced"
    [record] = caplog.records
    assert record.getMessage() == "pool not synced"
    fields = {k: getattr(record, k) for k in ("job_id", "expected", "listed", "overlap")}
    assert fields == {"job_id": "job-1", "expected": 3, "listed": 2, "overlap": 1}
    text = repr(record.__dict__)
    assert not any(i in text for i in [*WANTED, *stale])  # never an id
