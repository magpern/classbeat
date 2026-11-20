"""SD-P4 part 3 (#1301, ADR 0040 sections 8 and 9): shadow mode's pure rules, the wait budget, the
poll pace and the outcome codes (``shadow_policy``) and the record (``shadow_record_model``):
path (a) only, and no text of the track in it. No database, no network."""

import copy
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from classbeat.spotdown import shadow_policy as sp
from classbeat.spotdown.failure_policy import TrackFailure, Unreachable
from classbeat.spotdown.shadow_record_model import evaluate, local_metrics
from spotdownfake import example

SHA = "c" * 64
REFERENCE = "trk-secret-ref-01"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
LOCAL = {
    "duration": 12.0,
    "songs": [{"start": 0.0, "end": 12.0, "bpm": 128.0, "first_downbeat": 0.4}],
    "events": [],
    "sections": [
        {"start": 0.0, "end": 6.0, "energy": 0.0},
        {"start": 6.0, "end": 12.0, "energy": 1.0},
    ],
    "track_version": 1,
}


def doc(sha: str = SHA, kind: str = "upload") -> dict[str, Any]:
    document = copy.deepcopy(example("analysis-v1.minimal.json"))
    document["provenance"]["audio"]["sha256"] = sha
    document["provenance"]["source"]["kind"] = kind
    return document


@pytest.mark.parametrize(
    ("first", "left"),
    [(None, 600.0), (NOW, 600.0), (NOW - timedelta(seconds=90), 510.0),
     (NOW - timedelta(minutes=30), 0.0), (NOW + timedelta(minutes=5), 600.0)],
)  # fmt: skip
def test_the_budget_is_ten_minutes_from_the_jobs_first_relay(
    first: datetime | None, left: float
) -> None:
    assert sp.remaining(first, NOW) == pytest.approx(left)


@pytest.mark.parametrize(
    ("rounds", "retry_after", "left", "delay"),
    [(0, None, 600, 5), (1, None, 600, 10), (3, None, 600, 30), (9, None, 600, 30),
     (0, 45.0, 600, 45), (2, None, 7.5, 7.5), (0, 600.0, 12, 12), (0, None, 0, 0)],
)  # fmt: skip
def test_the_pace_doubles_honours_retry_after_and_stays_in_the_budget(
    rounds: int, retry_after: float | None, left: float, delay: float
) -> None:
    assert sp.poll_delay(rounds, retry_after, left) == delay


def test_failures_become_outcome_codes() -> None:
    assert sp.failure_outcome(Unreachable("connection")) == ("unreachable", "connection")
    assert sp.failure_outcome(TrackFailure("processing_failed")) == (
        "track_failure",
        "processing_failed",
    )
    assert sp.detail_code("Not A Code!") == "other"
    assert sp.detail_code(None) is None


def test_the_played_bytes_are_exact_on_path_a() -> None:
    record = evaluate(doc(), REFERENCE, SHA, LOCAL, 1)
    assert record.outcome == "compared"
    assert record.metrics is not None
    assert (record.metrics["timing_class"], record.metrics["timing_reason"]) == (
        "exact",
        "played_recording",
    )
    assert record.metrics["analyzer_version"] == "1.0.0"
    assert record.provenance is not None
    assert record.provenance["timing"]["class"] == "exact"  # type: ignore[index]


@pytest.mark.parametrize(("sha", "kind"), [("d" * 64, "upload"), (SHA, "youtube"), (SHA, "url")])
def test_other_bytes_or_another_source_are_never_exact(sha: str, kind: str) -> None:
    record = evaluate(doc(sha, kind), REFERENCE, SHA, LOCAL, 1)
    assert record.metrics is not None
    assert record.metrics["timing_class"] == "unusable"  # no match or alignment is given


def test_a_document_without_provenance_is_a_bad_document() -> None:
    broken = doc()
    del broken["provenance"]["audio"]
    assert evaluate(broken, REFERENCE, SHA, LOCAL, 1).outcome == "bad_document"
    broken = doc()
    del broken["provenance"]["analyzer"]
    assert evaluate(broken, REFERENCE, SHA, LOCAL, 1).outcome == "bad_document"


def test_the_record_holds_no_text_of_the_track() -> None:
    document = doc()
    document["provenance"]["source"]["ref"] = "Some Title - Artist"
    record = evaluate(document, REFERENCE, SHA, LOCAL, 1)
    stored = json.dumps([record.metrics, record.provenance])
    assert REFERENCE not in stored
    assert "Title" not in stored
    assert record.metrics is not None
    texts = {k: v for k, v in record.metrics.items() if isinstance(v, str)}
    assert set(texts) == {
        "timing_class",
        "timing_reason",
        "analyzer_name",
        "analyzer_version",
        "map",
    }
    assert local_metrics(LOCAL) == {"bpm_local": 128.0, "duration_local_s": 12.0}


def test_an_odd_analyzer_version_is_stored_as_other() -> None:
    document = doc()
    document["provenance"]["analyzer"]["analyzer_version"] = "v 1 <script>"
    record = evaluate(document, REFERENCE, SHA, LOCAL, 1)
    assert record.metrics is not None
    assert record.metrics["analyzer_version"] == "other"


def test_a_youtube_documents_video_id_is_not_kept_in_the_shadow_provenance() -> None:
    document = doc(kind="youtube")
    document["provenance"]["source"]["ref"] = "dQw4w9WgXcQ"
    record = evaluate(document, REFERENCE, SHA, LOCAL, 1)
    assert record.provenance is not None
    assert record.provenance["source"] == {"kind": "youtube", "ref": None}
    assert "dQw4w9WgXcQ" not in json.dumps(record.provenance)
