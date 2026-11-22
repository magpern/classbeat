"""#1369 (ADR 0036 amendment B, B4, B4a, B6, B10): the read-only mix choices preview and the
``apply_mix_choices`` job, on the fakes (Spotify, the SwedeBeat gateway, spotdown, ``FakeProvider``)
against Postgres as ``classbeat_app``. Never the network (``tests/netguard.py``).

- the preview stores nothing and answers another user's class as not found;
- the job calls no model and nothing outside (a script with no turn fails any model call), takes
  no credit and writes no ``agent_runs`` row; its revision is ``applied(proposal, choices)`` with
  the base as its parent, the proposal and the canonical choices in its provenance and its hash
  holding; a second apply starts from the proposal, never from the first apply;
- the workout follows the derived timeline (its total, an effort on a kept-whole song);
- a no-op stores nothing; the timeline names the mix fields of a studio draft.
"""

import json
from typing import Any

import pytest

from classbeat.classes.studio_timeline_schemas import DraftMix, TrackMix
from classbeat.design import revision
from classbeat.studio.mix_choice_model import MixChoices, canonical
from mixapplytools import (
    DJ,
    WHOLE,
    applied,
    counts,
    create,
    current,
    designed,
    digest,
    preview,
    preview_of,
)
from studiotools import StudioEnv
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")


async def timeline(rider: Any, class_id: Any) -> dict[str, Any]:
    answer = await rider.browser.get(f"/api/classes/{class_id}/timeline")
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_the_preview_stores_nothing_and_another_users_class_is_not_found(
    studio: StudioEnv,
) -> None:
    rider, class_id, proposal = await designed(studio)
    before = counts(studio, rider.user_id)
    requests = len(studio.gateway.requests)
    found = await preview_of(studio, rider, class_id, WHOLE)
    assert counts(studio, rider.user_id) == before
    assert len(studio.gateway.requests) == requests  # nothing asked of the gateway
    assert found["refusal"] is None
    assert found["proposal_revision_id"] == str(proposal)
    assert [w["start_sec"] for w in found["windows"]] == [0.0] * 8
    assert [w["end_sec"] for w in found["windows"]] == [240.0] * 8
    assert [w["position"] for w in found["windows"]] == list(range(8))
    assert found["crossfades"] == [0] * 7
    assert found["total_sec"] == 1920.0
    assert found["allowed"] == {"low_sec": 1620.0, "high_sec": 1980.0}
    assert len(found["suggestions"]) == 8
    assert found["suggestions"][1]["pieces"] == [{"start_sec": 0.0, "end_sec": 223.53}]
    assert all(s["full_only"] is None for s in found["suggestions"])
    text = json.dumps(found)
    for song in studio.songs.revision(proposal).music["mix_spec"]["tracks"]:
        assert song["reference"] not in text  # no catalogue reference leaves the server
    # Another user: the same request on this class is not found, and nothing is stored.
    other = await studio.rider()
    theirs = counts(studio, other.user_id)
    answer = await preview(other, class_id, proposal, digest(studio, proposal), WHOLE)
    assert answer.status_code == 404
    assert answer.json()["detail"] == {"reason": "not_found"}
    assert counts(studio, other.user_id) == theirs
    assert counts(studio, rider.user_id) == before
    # A stale base is refused by name.
    stale = await preview(rider, class_id, proposal, "0" * 64, WHOLE)
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "stale_base")


async def test_the_job_calls_no_model_takes_no_credit_and_stores_the_applied_spec(
    studio: StudioEnv,
) -> None:
    rider, class_id, proposal = await designed(studio)
    wanted = await preview_of(studio, rider, class_id, WHOLE)
    before = counts(studio, rider.user_id)
    requests = len(studio.gateway.requests)
    stored = await applied(studio, rider, class_id, {**WHOLE, "keep_whole": []})
    after = counts(studio, rider.user_id)
    assert after["agent_runs"] == before["agent_runs"]  # no model ran
    assert after["credit_ledger"] == before["credit_ledger"]
    assert after["class_revisions"] == before["class_revisions"] + 1
    assert len(studio.gateway.requests) == requests  # no download, no gateway call
    job = studio.songs.sql(
        "SELECT credit_state FROM jobs WHERE owner_id = :o AND kind = 'apply_mix_choices'",
        o=rider.user_id,
    ).one()
    assert job.credit_state == "none"
    spec = stored.music["mix_spec"]
    assert spec["crossfades"] == wanted["crossfades"]
    assert [(t["start_sec"], t["end_sec"]) for t in spec["tracks"]] == [
        (w["start_sec"], w["end_sec"]) for w in wanted["windows"]
    ]
    assert stored.parent_id == proposal
    assert stored.provenance["origin"] == "mix_choices"
    assert stored.provenance["proposal_revision_id"] == str(proposal)
    assert stored.provenance["choices"] == json.loads(canonical(MixChoices.whole_songs()))
    assert bytes(stored.content_hash) == revision.studio_hash(stored.content, spec)
    view = await timeline(rider, class_id)
    assert view["spec_digest"] == wanted["spec_digest"]
    # The workout follows the music: its total is the derived composed duration.
    assert abs(view["workout_sec"] - view["duration_sec"]) < 1
    assert view["duration_sec"] == 1920.0


async def test_a_second_apply_starts_from_the_proposal_and_efforts_keep_their_second(
    studio: StudioEnv,
) -> None:
    rider, class_id, proposal = await designed(studio)
    before = await timeline(rider, class_id)
    first = await applied(studio, rider, class_id, WHOLE)
    assert first.music["mix_spec"]["crossfades"] == [0] * 7
    # From the applied draft, keep track 1 whole and trim the rest as proposed: the crossfades are
    # the proposal's again (8), not the first apply's hard cuts.
    second = await applied(studio, rider, class_id, {**DJ, "keep_whole": [1]})
    assert second.parent_id == first.id
    assert second.provenance["proposal_revision_id"] == str(proposal)
    spec = second.music["mix_spec"]
    assert spec["crossfades"] == [8] * 7
    assert [t["end_sec"] for t in spec["tracks"]] == [240.0, 240.0, *[240.0, 223.53] * 3]
    after = await timeline(rider, class_id)
    assert after["proposal_revision_id"] == str(proposal)
    assert after["choices"] == {**DJ, "keep_whole": [1], "hard_cut_in": [], "hard_cut_out": []}
    assert after["is_plain"] is False
    assert abs(after["workout_sec"] - after["duration_sec"]) < 1
    # Each interval starting on a song keeps its second in that song (the remap's anchor): on the
    # kept-whole song (1) and before it to the whole second, on the later songs, which moved by
    # 16.47 s, up to the fit's snap to the nearest bar (1.875 s at 128 BPM) as well.
    starts_before = [t["mix_start_sec"] for t in before["tracks"]]
    starts_after = [t["mix_start_sec"] for t in after["tracks"]]
    assert starts_after[2] - starts_before[2] == pytest.approx(16.47)
    on_whole = 0
    for old, new in zip(before["intervals"], after["intervals"], strict=True):
        assert old["track"] == new["track"]
        k = old["track"]
        moved = (new["start_sec"] - starts_after[k]) - (old["start_sec"] - starts_before[k])
        assert abs(moved) <= (1 if k <= 1 else 1 + 1.875), (k, moved)
        on_whole += k == 1
    assert on_whole  # the check above covered an effort on the kept-whole song


async def test_a_no_op_stores_nothing(studio: StudioEnv) -> None:
    rider, class_id, _ = await designed(studio)
    first = await applied(studio, rider, class_id, WHOLE)
    before = counts(studio, rider.user_id)
    again = await create(studio, rider, class_id, WHOLE)
    assert (again.status_code, again.json()["detail"]["reason"]) == (409, "nothing_pending")
    assert counts(studio, rider.user_id) == before
    assert current(studio, class_id) == first.id


async def test_the_timeline_names_the_mix_fields_of_a_studio_draft(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    view = await timeline(rider, class_id)
    draft = DraftMix.model_validate({k: view[k] for k in DraftMix.model_fields})
    assert draft.proposal_revision_id == str(proposal)
    assert draft.choices is None  # a proposal records no choices
    assert draft.is_plain is False
    assert len(draft.spec_digest) == 64
    for track in view["tracks"]:
        found = TrackMix.model_validate({k: track[k] for k in TrackMix.model_fields})
        assert (found.length_sec, found.timing_class) == (240.0, "exact")
    whole = await applied(studio, rider, class_id, WHOLE)
    plain = await timeline(rider, class_id)
    assert plain["revision_id"] == str(whole.id)
    assert plain["is_plain"] is True
    assert plain["choices"] == {**WHOLE, "keep_whole": [], "hard_cut_in": [], "hard_cut_out": []}
    # A class that is no studio class has no timeline at all, so none of these fields.
    other = await studio.rider()
    made = await other.create()
    assert made.status_code == 201, made.text
    none = await other.browser.get(f"/api/classes/{made.json()['class_id']}/timeline")
    assert none.status_code in (404, 409)
    assert "spec_digest" not in none.text
