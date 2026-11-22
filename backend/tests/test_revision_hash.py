"""#1391 (ADR 0042 section 3): ``design.revision_hash``, the one hash. A draft with no overlay
hashes exactly as before (a MOWL draft, a studio draft); an overlay changes the hash, and any change
of it does; Save's check (``saving.approval._hash_holds``) accepts an intact overlay and refuses a
tampered or unreadable one; the hash of a draft about to be stored (``of_music``) takes the studio
spec only for a studio draft. Pure."""

import hashlib
import json
from typing import Any

import pytest

from agentfake import good_draft
from classbeat.design.revision import DraftRevision
from classbeat.design.revision_hash import edited_hash, of_music, revision_hash, studio_hash
from classbeat.jobs import JobFailure
from classbeat.saving import approval

CONTENT = good_draft()
SPEC = {"tracks": [{"reference": "r0", "start_sec": 0.0, "end_sec": 240.0}], "crossfades": []}
LOCK = {
    "type": "lock",
    "target": {"segment_kind": "main", "span": [300000, 540000]},
    "scope": "all",
}
OVERLAY = {"version": 1, "records": [LOCK]}


def sha(value: Any) -> bytes:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).digest()


def test_no_overlay_hashes_exactly_as_before() -> None:
    assert revision_hash(CONTENT) == sha(CONTENT)
    assert revision_hash(CONTENT, mix_spec=SPEC) == sha({"content": CONTENT, "mix_spec": SPEC})
    assert revision_hash(CONTENT, mix_spec=SPEC) == studio_hash(CONTENT, SPEC)


def test_an_overlay_changes_the_hash_and_every_change_of_it_does() -> None:
    plain, edited = revision_hash(CONTENT), revision_hash(CONTENT, overlay=OVERLAY)
    assert edited != plain
    assert edited == sha({"content": CONTENT, "overlay": OVERLAY})
    timing = {"version": 1, "records": [{**LOCK, "scope": "timing"}]}
    assert revision_hash(CONTENT, overlay=timing) != edited
    studio = revision_hash(CONTENT, mix_spec=SPEC, overlay=OVERLAY)
    assert studio == edited_hash(CONTENT, SPEC, OVERLAY)
    assert studio not in (edited, studio_hash(CONTENT, SPEC))


def test_save_accepts_an_intact_overlay_and_refuses_a_tampered_one() -> None:
    holds = approval._hash_holds  # pyright: ignore[reportPrivateUsage]
    stored = revision_hash(CONTENT, overlay=OVERLAY)
    assert holds(CONTENT, OVERLAY, stored)
    assert not holds(CONTENT, {"version": 1, "records": [{**LOCK, "scope": "timing"}]}, stored)
    assert not holds(CONTENT, None, stored)  # the overlay dropped
    assert holds(CONTENT, None, sha(CONTENT))  # no overlay: today's hash
    assert not holds(CONTENT, OVERLAY, sha(CONTENT))  # an overlay added
    unreadable = {"version": 1, "records": []}  # a cleared overlay is NULL, never empty
    assert not holds(CONTENT, unreadable, revision_hash(CONTENT, overlay=unreadable))
    assert not holds([], None, sha([]))  # content that is no draft


def test_of_music_takes_the_spec_of_a_studio_draft_only() -> None:
    studio = {"source": "swedebeat_studio", "mix_spec": SPEC}
    assert of_music(CONTENT, studio, None) == studio_hash(CONTENT, SPEC)
    assert of_music(CONTENT, {"source": "spotify", "mix_spec": SPEC}, None) == sha(CONTENT)
    assert of_music(CONTENT, studio, OVERLAY) == edited_hash(CONTENT, SPEC, OVERLAY)


async def test_a_revision_whose_hash_is_not_the_one_hash_is_never_stored() -> None:
    music = {"source": "swedebeat_studio", "mix_spec": SPEC}
    wrong = revision_hash(CONTENT, mix_spec=SPEC)  # the hash without the overlay it carries
    made = DraftRevision(CONTENT, wrong, music, {}, edit_overlay=OVERLAY)
    unused: Any = None  # the check runs before the session is touched
    with pytest.raises(JobFailure) as failed:
        await made.store(unused)
    assert failed.value.category == "internal"
