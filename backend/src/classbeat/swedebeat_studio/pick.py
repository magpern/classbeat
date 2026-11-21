"""A studio pick (ADR 0036 section 3): from the brief to the list's content, with no MOWL.

1. ``pool_search``: candidates from a Spotify search (``Steps.search``), or ``playlist_read``:
   the user's own Spotify playlist, read only and never modified (``Steps.read_playlist``).
2. ``pool_match``: their facts from one gateway match, with the evidence retry (``Steps.match``).
   **Suspected unavailability** (every sent candidate still missing, at least 3): no swaps and no
   choice; the list holds the candidates as gaps with ``suspected_unavailable``, for the user's
   Try again later or Swap anyway. ``swap_anyway`` is that explicit choice: the pick goes on.
3. ``swap``: while the usable candidates cannot fill the list (``candidate_policy.short``) and
   some are really missing, the song agent replaces them, at most ``MAX_ROUNDS`` (3) rounds: a
   ``pick_pool`` stage for the roles of the missing candidates the list needs (``to_replace``),
   its new candidates matched again (a round whose candidates are all missing ends the swaps,
   unless the user chose Swap anyway), and each replaced original paired with a usable new one of
   its role (``pair_swaps``). A high miss rate on its own never stops a pick.
4. ``choose``: the ``choose_songs`` stage over the usable candidates. A chosen replacement
   carries its swap record.
"""

from collections.abc import Mapping
from typing import Final

from classbeat.agent import PickPlan
from classbeat.agent.brief import Brief
from classbeat.agent.schemas import MAX_SONGS
from classbeat.swedebeat_studio.candidate_policy import (
    Candidate,
    gap_of,
    pair_swaps,
    short,
    song_of,
    to_replace,
    usable,
)
from classbeat.swedebeat_studio.evidence_policy import SUSPECTED, Evidence
from classbeat.swedebeat_studio.playlist_policy import PLAYLIST_NEED
from classbeat.swedebeat_studio.song_list_model import (
    MAX_ROUNDS,
    SOURCE,
    VERSION,
    StudioListContent,
    Swap,
)
from classbeat.swedebeat_studio.song_list_model import (
    Evidence as StoredEvidence,
)
from classbeat.swedebeat_studio.stages import Steps
from classbeat.swedebeat_studio.swap_policy import song_total

CROSSFADE_SEC: Final = 0.0  # whole songs, no crossfade (ADR 0036 B6)


def stored_evidence(found: Evidence, *, anyway: bool = False) -> StoredEvidence:
    """The match's evidence as the list stores it. ``anyway``: a suspected match the user chose
    to swap anyway, which makes the list ``ok`` by that choice (``anyway`` records it)."""
    anyway = anyway and found.state == SUSPECTED
    return StoredEvidence(
        state="ok" if anyway else found.state,
        sent=found.sent,
        missing_first=found.missing_first,
        missing=found.missing,
        anyway=anyway,
    )


def suspected(plan: PickPlan, pool: list[Candidate], found: Evidence) -> StudioListContent:
    """The list of a suspected match: the candidates as gaps, shown and never mixed."""
    songs = [gap_of(c) for c in pool[:MAX_SONGS]]
    return StudioListContent(
        v=VERSION,
        source=SOURCE,
        mode="pick",
        target_sec=plan.target_sec,
        total_sec=0,
        crossfade_sec=CROSSFADE_SEC,
        songs=songs,
        evidence=stored_evidence(found),
        rounds=0,
        note="",
    )


async def _swap_rounds(
    steps: Steps,
    brief: Brief,
    plan: PickPlan,
    pool: list[Candidate],
    *,
    swap_anyway: bool,
    need: Mapping[str, int],
) -> tuple[list[Candidate], dict[str, Swap], int]:
    found = [c for c in pool if usable(c)]
    missing = [c for c in pool if not usable(c)]
    seen = {c.spotify_id for c in pool}
    swaps: dict[str, Swap] = {}
    rounds = 0
    while missing and rounds < MAX_ROUNDS and short(found, plan.slots, need):
        rounds += 1
        wanted = to_replace(missing, found, plan.slots, need)[:MAX_SONGS]
        roles = [c.role for c in wanted]
        searched = await steps.search(PickPlan.build(brief, replace=roles), "swap")
        fresh = [c for c in searched if c.spotify_id not in seen]
        seen |= {c.spotify_id for c in searched}
        if not fresh:
            continue
        fresh, found_now = await steps.match(fresh)
        paired, left = pair_swaps(wanted, fresh)
        missing = left + [c for c in missing if c not in wanted]
        swaps |= paired
        found += [c for c in fresh if usable(c)]
        if found_now.state == SUSPECTED and not swap_anyway:
            break  # suspected again: no more automatic swaps
    return found, swaps, rounds


async def pick(
    steps: Steps, brief: Brief, *, swap_anyway: bool, playlist_id: str | None = None
) -> StudioListContent:
    """A pick from a Spotify search, or from the user's own playlist (``playlist_id``, read only;
    its songs need no role, so it swaps only while they cannot fill the list)."""
    plan = PickPlan.build(brief)
    steps.require_length(plan)  # mix_too_long, before any model call
    if playlist_id is not None:
        await steps.run.stage("playlist_read")
        pool = await steps.read_playlist(playlist_id)
        need = PLAYLIST_NEED
    else:
        pool = await steps.search(plan, "pool_search")
        need = plan.required_roles()
    await steps.run.stage("pool_match")
    pool, found_first = await steps.match(pool)
    if found_first.state == SUSPECTED and not swap_anyway:
        return suspected(plan, pool, found_first)
    found, swaps, rounds = await _swap_rounds(
        steps, brief, plan, pool, swap_anyway=swap_anyway, need=need
    )
    chosen = await steps.choose(plan, found)
    by_id = {c.spotify_id: c for c in found}
    songs = [song_of(by_id[s.spotify_id], s.role, swaps.get(s.spotify_id)) for s in chosen.songs]
    return StudioListContent(
        v=VERSION,
        source=SOURCE,
        mode="pick",
        target_sec=plan.target_sec,
        total_sec=song_total(songs),
        crossfade_sec=float(chosen.crossfade_sec),
        songs=songs,
        evidence=stored_evidence(found_first, anyway=swap_anyway),
        rounds=rounds,
        note=chosen.note[:300],
    )
