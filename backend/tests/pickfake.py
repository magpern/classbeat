"""Helpers for the song-picking tests (ADR 0008 section 3): the synthetic world of a smoke case,
recorded scripts for it, what a provider adapter would be sent, and a fake Spotify with a value
planted in every place the model must never see.

``world(case)`` builds the stage contexts of a ``PickCase`` (``classbeat.evals.pick``). Scripts are
dicts for ``FakeProvider``; the refs in them are the registry's, which are deterministic: a query
``group0`` finds tracks 0 to 9 of the synthetic catalogue, ``group1`` tracks 10 to 19, and refs
are given in the order tracks are first seen.
"""

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from mowl import spotify_web
from mowl.credentials import Credentials

from agentfake import CONFIG
from classbeat.agent import run_stage
from classbeat.agent.choose_songs import ChooseContext, choose_stage
from classbeat.agent.limits import Limits
from classbeat.agent.loop import AgentConfig
from classbeat.agent.pick_pool import PoolContext, SearchUnavailable, pool_stage
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.agent.registry import SearchRegistry
from classbeat.agent.songs import PickPlan, analysed_pool
from classbeat.evals import pick
from classbeat.evals.pick import PickCase, SyntheticCatalogue, plan_for
from spotifycore import FakeSpotify
from spotifyfake import CALLBACK, worker_opener

BRIEF = {"length_min": 30, "intensity": "moderate", "climbs": 1, "sprints": 2}
POOL_LIMITS = Limits(max_turns=12, max_tool_calls=30, max_corrections=3, job_cost_micros=3_000_000)
CHOOSE_LIMITS = Limits(max_turns=8, max_tool_calls=12, max_corrections=3, job_cost_micros=3_000_000)


def case(name: str = "pick_30", **overrides: Any) -> PickCase:
    base: dict[str, Any] = {
        "name": name,
        "about": "A test case.",
        "brief": BRIEF,
        "catalogue": {"size": 40},
    }
    return PickCase.model_validate_json(json.dumps({**base, **overrides}))


@dataclass
class World:
    case: PickCase
    plan: PickPlan
    catalogue: SyntheticCatalogue
    registry: SearchRegistry
    pool: PoolContext

    def fill(self, groups: int) -> None:
        """Registry refs as the first ``groups`` searches (``group0`` ...) would have given."""
        before = len(self.catalogue.queries)
        for g in range(groups):
            self.registry.add_hits(self.catalogue.search(f"group{g}"))
        del self.catalogue.queries[before:]  # not a search of the model's

    def choose_context(self, picks: list[tuple[int, str]]) -> ChooseContext:
        """The choose stage's context for a pool of ``(ref, role)`` (after MOWL's analysis)."""
        self.fill(max(ref for ref, _ in picks) // 10 + 1)
        by_id = self.catalogue.by_id()
        wanted = [(self.registry.spotify_id(ref) or "", role) for ref, role in picks]
        analysis = pick.analysis_map([by_id[i] for i, _ in wanted])
        tracks, omitted = analysed_pool(analysis, wanted)
        return ChooseContext.build(self.plan, tracks, omitted)


def world(c: PickCase | None = None, searcher: Callable[[str], Any] | None = None) -> World:
    c = c or case()
    catalogue = SyntheticCatalogue(c.catalogue)
    registry = SearchRegistry()
    plan = plan_for(c)
    return World(
        c, plan, catalogue, registry, PoolContext(plan, registry, searcher or catalogue.search)
    )


def call(name: str, input: dict[str, Any] | None = None, **checks: Any) -> dict[str, Any]:
    return {"name": name, "input": input or {}, **checks}


def turn(*calls: dict[str, Any]) -> dict[str, Any]:
    return {"calls": list(calls)}


def script(*turns: dict[str, Any]) -> FakeScript:
    return FakeScript.model_validate_json(json.dumps({"model": "fake-model", "turns": list(turns)}))


def provider(*turns: dict[str, Any]) -> FakeProvider:
    return FakeProvider(script(*turns))


def entries(*pairs: tuple[int, str]) -> dict[str, Any]:
    return {"entries": [{"ref": r, "role": role} for r, role in pairs]}


def songs(*items: tuple[str, int, str], note: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"songs": [{"source": s, "index": i, "role": r} for s, i, r in items]}
    if note:
        out["note"] = note
    return out


# The 30-minute pick of the smoke set: 14 candidates (two warmup, two build, two climb, four
# sprint, two recovery, two cooldown) found by two searches, then eight songs.
POOL_14 = [
    (0, "warmup"), (1, "warmup"), (2, "build"), (3, "build"), (4, "climb"), (5, "climb"),
    (6, "sprint"), (7, "sprint"), (8, "sprint"), (9, "sprint"), (10, "recovery"),
    (11, "recovery"), (12, "cooldown"), (13, "cooldown"),
]  # fmt: skip
LIST_8 = [
    ("pool", 0, "warmup"), ("pool", 2, "build"), ("pool", 4, "climb"),
    ("pool", 10, "recovery"), ("pool", 6, "sprint"), ("pool", 7, "sprint"),
    ("pool", 11, "recovery"), ("pool", 12, "cooldown"),
]  # fmt: skip


def pool_stage_turns() -> list[dict[str, Any]]:
    return [
        turn(
            call("search_tracks", {"query": "group0"}, expect={"ok": True}),
            call("search_tracks", {"query": "group1", "year_from": 2000, "year_to": 2024}),
        ),
        turn(call("check_pool", entries(*POOL_14), expect={"ok": True, "valid": True})),
        turn(call("submit_pool", entries(*POOL_14), expect={"accepted": True})),
    ]


def choose_stage_turns(note: str = "A steady build to the climb.") -> list[dict[str, Any]]:
    return [
        turn(call("get_pool", {}, expect={"ok": True}, contains=['"tracks"'])),
        turn(call("check_song_list", songs(*LIST_8), expect={"valid": True})),
        turn(call("submit_song_list", songs(*LIST_8, note=note), expect={"accepted": True})),
    ]


def good_pick_provider() -> FakeProvider:
    return provider(*pool_stage_turns(), *choose_stage_turns())


async def run_pool(
    w: World,
    fake: ModelProvider,
    host: Any,
    limits: Limits = POOL_LIMITS,
    config: AgentConfig = CONFIG,
) -> Any:
    from dataclasses import replace

    config = replace(config, limits=limits)
    return await run_stage(host, fake, pool_stage(w.pool), w.pool.first_message(), config)


async def run_choose(
    ctx: ChooseContext,
    fake: ModelProvider,
    host: Any,
    limits: Limits = CHOOSE_LIMITS,
    config: AgentConfig = CONFIG,
) -> Any:
    from dataclasses import replace

    config = replace(config, limits=limits)
    return await run_stage(host, fake, choose_stage(ctx), ctx.first_message(), config)


async def run_both(w: World, fake: FakeProvider, host: Any) -> tuple[Any, Any]:
    """Both stages: the pool, then the choose stage over the pool as MOWL would analyse it."""
    pooled = await run_pool(w, fake, host)
    ctx = w.choose_context([(p.ref, p.role) for p in pooled.output.picks])
    return pooled, await run_choose(ctx, fake, host)


def everything_sent(fake: FakeProvider) -> str:
    """What a provider adapter would send, as one string (as ``test_agent_model_input``)."""
    parts: list[str] = []
    for sent in fake.sent:
        spec = sent.spec
        parts += [spec.system, spec.first_message, spec.model, spec.effort, spec.stage]
        parts += [json.dumps([t.name, t.description, dict(t.input_schema)]) for t in spec.tools]
        for results, reminder in sent.turns:
            parts += [r.content for r in results] + [r.call_id for r in results]
            parts.append(reminder or "")
    return "\n".join(parts)


# --- a fake Spotify with a value planted in every place -------------------------------------------

PLANTED_USER = "planted-spotify-user-77"
PLANTED_DISPLAY = "Planted Display Name Q7x"
PLANTED_EMAIL = "planted.rider@example.test"
PLANTED_CLIENT = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
PLANTED_PLAYLIST_NAME = "Planted Playlist Name Z9y"
PLANTED_PLAYLIST_NAME_2 = "Planted Other Playlist Name K4w"


@dataclass
class PlantedSpotify:
    fake: FakeSpotify
    token: str
    refresh: str
    playlist_ids: list[str]

    def values(self) -> list[str]:
        return [
            PLANTED_USER,
            PLANTED_DISPLAY,
            PLANTED_EMAIL,
            PLANTED_CLIENT,
            PLANTED_PLAYLIST_NAME,
            PLANTED_PLAYLIST_NAME_2,
            self.token,
            self.refresh,
            *self.playlist_ids,
        ]


def planted_spotify(monkeypatch: pytest.MonkeyPatch) -> PlantedSpotify:
    """A fake Spotify whose user, playlists, app and token are planted values, whose search
    answers carry them in extra fields of every track, and which the library reaches through the
    worker's real allowlisted opener."""
    fake = FakeSpotify()
    fake.add_user(PLANTED_USER, PLANTED_DISPLAY)
    fake.add_app(PLANTED_CLIENT, redirect_uris=[CALLBACK], allowed=[PLANTED_USER])
    token, refresh = fake.grant(PLANTED_CLIENT, PLANTED_USER)
    first = fake.add_playlist(PLANTED_USER, PLANTED_PLAYLIST_NAME, description=PLANTED_EMAIL)
    second = fake.add_playlist(PLANTED_USER, PLANTED_PLAYLIST_NAME_2, public=True)
    plain = fake._track  # pyright: ignore[reportPrivateUsage]

    def planted_track(tid: str) -> dict[str, Any]:
        out = plain(tid)
        owner = {"id": PLANTED_USER, "display_name": PLANTED_DISPLAY, "email": PLANTED_EMAIL}
        out["added_by"] = owner
        out["owner"] = owner
        out["playlist"] = {"id": first.id, "name": PLANTED_PLAYLIST_NAME}
        out["playlists"] = [{"id": second.id, "name": PLANTED_PLAYLIST_NAME_2}]
        out["client_id"] = PLANTED_CLIENT
        out["access_token"] = token
        out["refresh_token"] = refresh
        out["album"]["owner"] = owner
        out["artists"][0]["email"] = PLANTED_EMAIL
        return out

    monkeypatch.setattr(fake, "_track", planted_track)
    monkeypatch.setattr(urllib.request, "_opener", worker_opener(fake))
    return PlantedSpotify(fake, token, refresh, [first.id, second.id])


def spotify_searcher(planted: PlantedSpotify) -> Callable[[str], Any]:
    """The pool stage's ``Searcher`` over the library's ``search_tracks`` and the fake."""
    creds = Credentials(
        spotify_client_id=PLANTED_CLIENT,
        spotify_access_token=planted.token,
        spotify_expires_at=time.time() + 3600,
        spotify_refresh_enabled=False,
        spotify_max_retry_wait=0,
    )

    def search(query: str) -> Any:
        try:
            return spotify_web.search_tracks(creds, query)
        except spotify_web.MowlError:
            raise SearchUnavailable from None

    return search
