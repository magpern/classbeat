"""#1451: ``design.revision_overlay``'s gates around a carried overlay. Records on a base the
editor never writes fail closed; the carried overlay is stored only as a read accepts it and
under the one hash; anything else is ``internal``; the base again is ``invalid_output``. Pure
(``DraftRevision`` is a record; nothing is stored)."""

import pytest

from classbeat.design import revision_overlay
from classbeat.design.edit_overlay_model import EMPTY, of_records
from classbeat.design.revision import DraftRevision
from classbeat.design.revision_hash import revision_hash
from classbeat.design.revision_overlay import base_overlay, carried, only_spotify
from classbeat.jobs import JobFailure
from test_revise_overlay import BASE, lock

DRAFT = DraftRevision(
    content=BASE,
    content_hash=revision_hash(BASE),
    music={"source": "mowl_spotify_playlist"},
    provenance={"origin": "revise_draft"},
)


def category(call: object) -> str:
    with pytest.raises(JobFailure) as failed:
        call()  # pyright: ignore[reportCallIssue]
    return failed.value.category


def test_records_on_a_base_the_editor_never_writes_fail_closed() -> None:
    only_spotify(EMPTY, False)  # nothing to carry: any base
    only_spotify(of_records([lock(0)]), True)  # a cycling Spotify draft's own edits
    assert category(lambda: only_spotify(of_records([lock(0)]), False)) == "internal"


def test_the_carried_overlay_is_stored_under_the_one_hash() -> None:
    overlay = of_records([lock(0)])
    made = carried(DRAFT, overlay, b"\0" * 32)
    assert made.edit_overlay == overlay.stored()
    assert made.content_hash == revision_hash(BASE, overlay=overlay.stored())
    assert (made.music, made.provenance, made.content) == (DRAFT.music, DRAFT.provenance, BASE)
    assert carried(DRAFT, None, None) is DRAFT  # no overlay: the draft as it was built


def test_anything_but_an_overlay_or_the_base_again_is_refused() -> None:
    overlay = of_records([lock(0)])
    assert category(lambda: carried(DRAFT, {"version": 1}, None)) == "internal"
    again = revision_hash(BASE, overlay=overlay.stored())
    assert category(lambda: carried(DRAFT, overlay, again)) == "invalid_output"


def test_a_base_overlay_holds_only_with_its_hash() -> None:
    stored = of_records([lock(0)]).stored()
    hashed = revision_hash(BASE, overlay=stored)
    assert base_overlay(BASE, stored, hashed, hashed).records
    assert category(lambda: base_overlay(BASE, stored, revision_hash(BASE), hashed)) == "internal"
    plain = revision_hash(BASE)
    assert category(lambda: base_overlay(BASE, stored, plain, plain)) == "internal"
    bad = {"version": 1, "records": [{"type": "bogus"}]}
    odd = revision_hash(BASE, overlay=bad)
    assert category(lambda: base_overlay(BASE, bad, odd, odd)) == "internal"


def test_an_overlay_that_would_not_read_back_is_never_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_overlay: object) -> None:
        raise ValueError("does not read back")

    monkeypatch.setattr(revision_overlay, "storable", refuse)
    assert category(lambda: carried(DRAFT, of_records([lock(0)]), None)) == "internal"
