"""Recorded ``FakeProvider`` scripts for the song-choice cases of the suite (ADR 0012 section 4.1,
issue #208), built from the case: what a careful model does, as turns with fixed usage numbers.

``script_for(case)`` replays the searches against the synthetic catalogue to learn each track's ref
(the registry numbers tracks in the order they are first seen), gives every slot of the class a
role, takes for each role the tracks that fit its BPM band in the manifest, submits a pool and then
the list. ``script_for(case, flaw=...)`` is the same with one flaw the stages do not refuse:

- ``"band"``: the first climb is a track far outside the climb band;
- ``"years"``: the searches carry no year filter, so the pool holds candidates out of the era.
"""

from typing import Any

from classbeat.agent.pick_pool import build_query
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.agent.registry import SearchRegistry
from classbeat.agent.schemas import SearchInput
from classbeat.agent.songs import PickPlan
from classbeat.evals import suite
from classbeat.evals.pick import CatalogueTrack, PickCase, SyntheticCatalogue, plan_for
from classbeat.evals.song_checks import Band
from pickfake import call, entries, songs, turn

BANDS = {r: Band.model_validate(b) for r, b in suite.read_manifest_data()["bpm_bands"].items()}
DROPS = 2  # every synthetic track has two drops
LOW_FIRST = ("warmup", "build", "recovery", "cooldown")  # slow roles: the slowest songs go there
OPENERS = 2


def _usage(inp: int, out: int, cached: int = 0) -> dict[str, int]:
    return {"input": inp, "output": out, "cache_read": cached, "cache_write": 0}


def _t(*calls: dict[str, Any], usage: dict[str, int]) -> dict[str, Any]:
    return {**turn(*calls), "usage": usage}


def slot_roles(plan: PickPlan) -> list[str]:
    """The role of each slot of a pick: warmup, build, the main part, a cooldown."""
    if plan.replace:
        raise ValueError("a replace case names its roles itself")
    b = plan.brief
    main = ["climb"] * b.climbs + ["sprint"] * b.sprints + ["race"] * b.races
    room = plan.n - 3
    if len(main) >= room:
        body = main[:room]
    else:
        body = []
        for role in main:
            body += [role, "recovery"]
        body = (body + ["build"] * room)[:room]
    return ["warmup", "build", *body, "cooldown"]


def _found(
    case: PickCase, years: list[int] | None
) -> tuple[list[dict[str, Any]], list[CatalogueTrack]]:
    """The search calls of the pool turn and the tracks they find, in ref order."""
    catalogue = SyntheticCatalogue(case.catalogue)
    registry = SearchRegistry()
    calls = []
    for g in range((case.catalogue.size + 9) // 10):
        args: dict[str, Any] = {"query": f"group{g}"}
        if years:
            args |= {"year_from": years[0], "year_to": years[1]}
        calls.append(call("search_tracks", args))
        registry.add_hits(catalogue.search(build_query(SearchInput.model_validate(args))))
    by_id = catalogue.by_id()
    ordered = [by_id[registry.spotify_id(r) or ""] for r in range(len(registry))]
    return calls, ordered


def _fits(role: str, t: CatalogueTrack) -> bool:
    return BANDS[role].holds(t.bpm, DROPS)


def _take(role: str, free: list[CatalogueTrack]) -> CatalogueTrack:
    ok = sorted((t for t in free if _fits(role, t)), key=lambda t: t.bpm)
    if not ok:
        raise LookupError(f"no free track fits {role}")
    pick = ok[0] if role in LOW_FIRST else ok[-1]
    free.remove(pick)
    return pick


def _outside(role: str, free: list[CatalogueTrack]) -> CatalogueTrack:
    pick = next(t for t in free if not _fits(role, t))
    free.remove(pick)
    return pick


def script_for(case: PickCase, flaw: str | None = None) -> FakeScript:
    plan = plan_for(case)
    years = list(case.expect.years) if case.expect.years and flaw != "years" else None
    searches, ordered = _found(case, years)
    ref = {t.spotify_id: i for i, t in enumerate(ordered)}
    free = list(ordered)
    wanted = list(plan.replace) if case.replace else slot_roles(plan)  # replace: slot order
    taken: dict[int, tuple[str, CatalogueTrack]] = {}
    # The slowest songs go first to the opener and the closer, so the main part rises above them.
    order = sorted(range(len(wanted)), key=lambda n: wanted[n] not in ("warmup", "cooldown"))
    climbs_done = False
    for n in order:
        role = wanted[n]
        bad = flaw == "band" and role == "climb" and not climbs_done
        climbs_done = climbs_done or role == "climb"
        taken[n] = (role, _outside(role, free) if bad else _take(role, free))
    chosen = [taken[n] for n in range(len(wanted))]
    pool = list(chosen)
    need = dict(plan.required_roles())
    have: dict[str, int] = {}
    for role, _ in pool:
        have[role] = have.get(role, 0) + 1
    extra_roles = [r for r, k in need.items() for _ in range(max(0, k - have.get(r, 0)))]
    if not plan.replace and have.get("warmup", 0) + have.get("build", 0) < OPENERS:
        extra_roles.append("build")
    low = plan.pool_range[0]
    while len(extra_roles) + len(pool) < low:
        extra_roles.append("build")
    for role in extra_roles:
        pool.append((role, _take(role, free)))
    if len(pool) > plan.pool_range[1]:
        raise ValueError("the case needs a bigger pool than the stage allows")
    pool_entries = entries(*[(ref[t.spotify_id], role) for role, t in pool])
    index = {t.spotify_id: i for i, (_, t) in enumerate(pool)}
    items: list[tuple[str, int, str]] = []
    if case.replace:
        spec = case.replace
        mine = iter(chosen)
        kept_at = 0
        for slot, role in enumerate(spec.seed_roles):
            if slot in spec.replace:
                _, t = next(mine)
                items.append(("pool", index[t.spotify_id], role))
            else:
                items.append(("kept", kept_at, role))
                kept_at += 1
    else:
        items = [("pool", index[t.spotify_id], role) for role, t in chosen]
    listed = songs(*items)
    final = songs(*items, note="Built to peak in the main part.")
    contains = ['"tracks"'] if not case.replace else ["kept_songs"]
    turns = [
        _t(*searches, usage=_usage(2600, 260 * len(searches))),
        _t(call("check_pool", pool_entries, expect={"valid": True}), usage=_usage(5200, 700)),
        _t(
            call("submit_pool", pool_entries, expect={"accepted": True}),
            usage=_usage(6100, 700, 3000),
        ),
        _t(call("get_pool", {}, expect={"ok": True}, contains=contains), usage=_usage(2300, 180)),
        _t(call("check_song_list", listed, expect={"valid": True}), usage=_usage(7600, 900)),
        _t(
            call("submit_song_list", final, expect={"accepted": True}),
            usage=_usage(8500, 400, 4000),
        ),
    ]
    return FakeScript.model_validate({"model": "fake-model", "turns": turns})


def provider_for(case: PickCase, flaw: str | None = None) -> FakeProvider:
    return FakeProvider(script_for(case, flaw))
