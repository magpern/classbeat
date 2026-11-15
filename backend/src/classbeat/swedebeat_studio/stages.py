"""The studio pick's steps (ADR 0036 section 3), shared by every action of ``studio_songs``.

- ``read_playlist``: the user's own Spotify playlist, read only (``playlist_policy``); never
  modified.
- ``search``: the song pick's ``pick_pool`` stage (the model names candidates by registry ``ref``;
  ``search_tracks`` through the job's Spotify connection). The candidates keep the search hit's
  Spotify title, artist and cover; no Spotify write.
- ``match``: one gateway match of the candidates with the evidence retry (``match.match_pairs``).
- ``choose``: the ``choose_songs`` stage over the usable candidates, their BPM and length the
  **SwedeBeat** facts, with the relaxed upper bound (``length_policy.UPPER``).

**Never in a model's input**: the catalogue references, tokens, the Spotify user, playlist or
track ids, covers and MOWL ids (the stages see refs, indexes, titles and numbers only). Every
stage run is kept in ``runs`` for the list's provenance; a model stage's host starts its job stage
(``pool_search``, ``swap``, ``choose``).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from mowl import spotify_web
from mowl.credentials import Credentials
from pydantic import SecretStr

from classbeat.agent import (
    AgentConfig,
    ChooseContext,
    ChosenList,
    JobStageHost,
    PickPlan,
    PoolContext,
    PoolResult,
    SearchRegistry,
    StageOutcome,
    choose_stage,
    pool_stage,
    run_stage,
)
from classbeat.agent import choose_songs as choose_module
from classbeat.agent import pick_pool as pool_module
from classbeat.agent.provider import ModelProvider
from classbeat.agent.schemas import MAX_POOL
from classbeat.agent.songs import PoolTrack
from classbeat.connections import OpenConnection
from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobFailure, JobRun
from classbeat.playlists import common
from classbeat.songs.config import SongConfig
from classbeat.songs.pick import stage_config
from classbeat.swedebeat_studio.candidate_policy import Candidate, facts_of
from classbeat.swedebeat_studio.evidence_policy import Evidence
from classbeat.swedebeat_studio.length_policy import UPPER
from classbeat.swedebeat_studio.match import match_pairs
from classbeat.swedebeat_studio.playlist_policy import playlist_candidates


@dataclass
class Steps:
    """One run's model, searcher and gateway, and the stage runs so far."""

    run: JobRun
    provider: ModelProvider
    agent: AgentConfig
    config: SongConfig
    searcher: Any
    bearer: SwedebeatBearer
    key: SecretStr
    keyring: KeyRing
    spotify: OpenConnection
    runs: list[StageOutcome] = field(default_factory=list[StageOutcome])

    async def read_playlist(self, playlist_id: str) -> list[Candidate]:
        """The user's own playlist's candidates: one read (``playlist_items``), never a write."""

        def read(creds: Credentials) -> list[Any]:
            return spotify_web.playlist_items(creds, playlist_id, MAX_POOL)

        entries = await common.call(
            self.run.factory, self.keyring, self.run.ctx, self.spotify, read
        )
        found = playlist_candidates(entries)
        if not found:
            raise JobFailure("pool_too_small")  # nothing playable to match
        return found

    async def search(self, plan: PickPlan, job_stage: str) -> list[Candidate]:
        """The ``pick_pool`` stage's candidates for ``plan`` (not matched yet)."""
        registry = SearchRegistry()
        ctx = PoolContext(plan, registry, self.searcher)
        limits = stage_config(
            self.agent, self.config, turns=pool_module.MAX_TURNS, calls=pool_module.MAX_TOOL_CALLS
        )
        outcome = await run_stage(
            JobStageHost(self.run, job_stage), self.provider, pool_stage(ctx), ctx.first_message(),
            limits,
        )  # fmt: skip
        if not isinstance(outcome.output, PoolResult) or not outcome.output.picks:
            raise JobFailure("internal")
        self.runs.append(outcome)
        found: list[Candidate] = []
        for pick in outcome.output.picks:
            entry = registry.get(pick.ref)
            if entry is None:  # submit_pool checked every ref
                raise JobFailure("internal")
            found.append(
                Candidate(pick.spotify_id, entry.title, entry.artist, pick.role, pick.cover)
            )
        return found

    async def match(self, candidates: Sequence[Candidate]) -> tuple[list[Candidate], Evidence]:
        """The candidates with their final verdicts, and the match's evidence."""
        pairs = [(c.title, c.artist) for c in candidates]
        matched = await match_pairs(self.run, self.bearer, self.key, pairs)
        out = [replace(c, verdict=v) for c, v in zip(candidates, matched.verdicts, strict=True)]
        return out, matched.evidence

    async def choose(self, plan: PickPlan, candidates: Sequence[Candidate]) -> ChosenList:
        """The ``choose_songs`` stage over the usable candidates (``pool_too_small`` when too few
        are usable). A candidate without SwedeBeat facts is left out and counted."""
        tracks: list[PoolTrack] = []
        for c in candidates:
            facts = facts_of(c.verdict)
            if facts is None:
                continue
            tracks.append(
                PoolTrack(
                    spotify_id=c.spotify_id,
                    title=c.title,
                    artist=c.artist,
                    role=c.role,
                    bpm=facts.bpm,
                    length_sec=facts.duration_sec,
                    cover=c.cover,
                )
            )
        ctx = ChooseContext.build(plan, tracks, len(candidates) - len(tracks), upper=UPPER)
        limits = stage_config(
            self.agent,
            self.config,
            turns=choose_module.MAX_TURNS,
            calls=choose_module.MAX_TOOL_CALLS,
        )
        outcome = await run_stage(
            JobStageHost(self.run, "choose"), self.provider, choose_stage(ctx),
            ctx.first_message(), limits,
        )  # fmt: skip
        if not isinstance(outcome.output, ChosenList):
            raise JobFailure("internal")
        self.runs.append(outcome)
        return outcome.output
