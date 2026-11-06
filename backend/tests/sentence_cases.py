"""Every server sentence code with the numbers that pick its plural form, as the backend words it.

``tests/fixtures/sentence_codes.json`` is this list written out; the frontend test
(``src/i18n/sentences.test.ts``) reads the same file and checks the English catalogue gives the
same text, so a code or a sentence cannot change on one side only (ADR 0023 section 6)."""

from datetime import UTC, datetime
from typing import Any

from classbeat.jobs.sentences import SONG_SENTENCES, job_sentence_of
from classbeat.sentence import Sentence
from classbeat.spotify.errors import CATEGORIES, sentence_of

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _swedebeat(state: str, category: str | None, hint: int | None) -> Sentence:
    found = job_sentence_of(state, category, "rate_limited", hint, _NOW, _NOW, "swedebeat_match")
    assert found is not None
    return found


def all_sentences() -> list[Sentence]:
    found = [sentence_of(c) for c in (*CATEGORIES, "unknown")]
    found += [sentence_of("rate_limited", s) for s in (60, 180)]
    found += [sentence_of("quota_exhausted", s) for s in (7200, 3600 * 3)]
    found += [
        s for c in SONG_SENTENCES if (s := job_sentence_of("failed", c, None, None, _NOW, _NOW))
    ]
    found += [_swedebeat("queued", None, h) for h in (60, 180)]
    found.append(_swedebeat("failed", "rate_limited", None))
    return found


def as_json() -> list[dict[str, Any]]:
    seen: set[tuple[str, tuple[tuple[str, int], ...]]] = set()
    out: list[dict[str, Any]] = []
    for s in all_sentences():
        key = (s.code, tuple(sorted(s.params.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": s.code, "params": s.params, "english": s.text})
    return out
