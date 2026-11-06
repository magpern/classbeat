"""Server sentences as codes (ADR 0023 section 6, ML4): every code has its English text, the
numbers are whole, and no provider text is in a sentence."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from classbeat.jobs.sentences import SONG_SENTENCES, job_sentence, job_sentence_of
from classbeat.spotify.errors import CATEGORIES, SENTENCES, sentence, sentence_of
from sentence_cases import all_sentences, as_json

GOLDEN = Path(__file__).parent / "fixtures" / "sentence_codes.json"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_the_golden_file_is_what_the_backend_says() -> None:
    # Regenerate with ``sentence_cases.as_json()`` when a sentence is meant to change; the
    # frontend test reads the same file.
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == as_json()


def test_every_code_is_a_dotted_lowercase_name_with_whole_number_params() -> None:
    for found in all_sentences():
        assert re.fullmatch(r"(spotify|job|swedebeat)\.[a-z_]+", found.code), found.code
        assert set(found.params) <= {"count"}
        assert all(isinstance(v, int) and v >= 1 for v in found.params.values())
        assert "{" not in found.text


def test_every_spotify_category_and_song_sentence_has_a_code() -> None:
    codes = {s.code for s in all_sentences()}
    assert {f"spotify.{c}" for c in CATEGORIES} <= codes
    assert {f"job.{c}" for c in SONG_SENTENCES} <= codes
    assert {"spotify.unknown", "swedebeat.wait", "swedebeat.stopped"} <= codes


def test_the_text_is_unchanged_and_the_numbers_are_the_wait() -> None:
    assert sentence("rate_limited", 30) == sentence_of("rate_limited", 30).text
    assert sentence_of("rate_limited", 150).params == {"count": 3}
    assert sentence_of("rate_limited", 150).text.endswith("in about 3 minutes.")
    assert sentence_of("quota_exhausted", 7200).params == {"count": 2}
    assert sentence_of("invalid_grant").params == {}
    assert sentence_of("invalid_grant").text == SENTENCES["invalid_grant"]


def test_an_unknown_category_is_the_generic_code_with_the_generic_text() -> None:
    found = sentence_of("whatever the provider said")
    assert found.code == "spotify.unknown"
    assert "provider" not in found.text
    assert found.text == "Something went wrong with Spotify. Try again later."


def test_job_sentences_carry_code_and_text_together() -> None:
    failed = job_sentence_of("failed", "pool_busy", None, None, _NOW, _NOW)
    assert failed is not None
    assert (failed.code, failed.text) == ("job.pool_busy", SONG_SENTENCES["pool_busy"])
    assert job_sentence("failed", "pool_busy", None, None, _NOW, _NOW) == failed.text
    assert job_sentence_of("failed", "internal", None, None, _NOW, _NOW) is None
    assert job_sentence_of("running", None, None, None, _NOW, _NOW) is None


def test_a_waiting_match_names_its_minutes_as_a_number() -> None:
    found = job_sentence_of("queued", None, "rate_limited", 180, _NOW, _NOW, "swedebeat_match")
    assert found is not None
    assert (found.code, found.params) == ("swedebeat.wait", {"count": 3})
    assert found.text == "Swedebeat asks to slow down; trying again in about 3 minutes."
