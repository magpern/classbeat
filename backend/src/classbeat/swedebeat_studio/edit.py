"""The user's changes to a studio list (ADR 0036 section 3.3): undo a swap, ask for another song.

- ``undone``: the swap at the position undone (``swap_policy.undo``): the original is back as a
  marked gap, which can never go into a mix. No model, no catalogue call.
- ``another``: another song for a swapped slot or a gap. The other songs stay in their slots
  (``another_slots``: kept for the choose stage, other gaps left out and put back after). At most
  ``MAX_ROUNDS`` (3) searches, each one's new candidates matched again; a round whose candidates
  are all missing (suspected) ends the search. The new song carries a swap record of reason
  ``asked`` naming the slot's first original. Too few usable candidates is ``pool_too_small``,
  and the seed list stays the class's newest.
"""

from classbeat.agent import PickPlan
from classbeat.agent.brief import Brief
from classbeat.swedebeat_studio.candidate_policy import Candidate, song_of, swap_of, usable
from classbeat.swedebeat_studio.evidence_policy import OK, SUSPECTED, Evidence
from classbeat.swedebeat_studio.pick import CROSSFADE_SEC, stored_evidence
from classbeat.swedebeat_studio.song_list_model import MAX_ROUNDS, StudioListContent, StudioSong
from classbeat.swedebeat_studio.stages import Steps
from classbeat.swedebeat_studio.swap_policy import (
    another_slots,
    asked_original,
    expand,
    song_total,
    undo,
)


def undone(seed: StudioListContent, position: int) -> StudioListContent:
    return undo(seed, position).model_copy(update={"mode": "replace", "rounds": 0})


async def another(
    steps: Steps, brief: Brief, seed: StudioListContent, position: int
) -> StudioListContent:
    steps.require_length(PickPlan.build(brief))  # mix_too_long, before any model call
    slots = another_slots(seed, position)
    asked = seed.songs[position]
    original = asked_original(asked)
    plan = PickPlan.build(brief, replace=[slots.role], kept=slots.kept)
    exclude = set(seed.track_ids()) | {original.spotify_id}
    found: list[Candidate] = []
    first: Evidence | None = None
    rounds = 0
    while rounds < MAX_ROUNDS and not any(c.role == slots.role for c in found):
        rounds += 1
        searched = await steps.search(plan, "pool_search" if rounds == 1 else "swap")
        fresh = [c for c in searched if c.spotify_id not in exclude]
        exclude |= {c.spotify_id for c in searched}
        if not fresh:
            continue
        await steps.run.stage("pool_match")
        fresh, found_now = await steps.match(fresh)
        first = first or found_now
        found += [c for c in fresh if usable(c)]
        if found_now.state == SUSPECTED:
            break
    chosen = await steps.choose(plan, found)  # pool_too_small: nothing usable came back
    kept = {s.spotify_id: s for s in seed.songs if not s.gap}
    by_id = {c.spotify_id: c for c in found}
    compressed: list[StudioSong] = []
    for song in chosen.songs:
        if not song.new:
            compressed.append(kept[song.spotify_id])
            continue
        fresh_one = by_id[song.spotify_id]
        compressed.append(song_of(fresh_one, song.role, swap_of(original, fresh_one, "asked")))
    songs = expand(compressed, slots.gaps)
    evidence = stored_evidence(first) if first is not None else seed.evidence
    if evidence.state != OK:
        evidence = seed.evidence
    return seed.model_copy(
        update={
            "mode": "replace",
            "songs": songs,
            "total_sec": song_total(songs),
            "crossfade_sec": CROSSFADE_SEC,
            "evidence": evidence,
            "rounds": rounds,
            "note": chosen.note[:300],
        }
    )
