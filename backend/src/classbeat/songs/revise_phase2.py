"""Phase 2 of ``revise_songs`` (ADR 0008 section 6), split out of ``classbeat.songs.revise``: the
approved list's final playlist and music (``classbeat.songs.phase2.final_music``), the request's
reading layered onto the class's brief (``classbeat.songs.revise_reading``: the stored reading of
the approved list, no second model call), the ``revise`` stage and the final transaction.
"""

import hashlib
import json
from typing import Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.agent import AgentConfig, Brief, JobStageHost, run_stage
from classbeat.agent.provider import ModelProvider
from classbeat.agent.revise import ReviseContext, ReviseResult, revise_stage
from classbeat.agent.stored_draft import stored_draft
from classbeat.crypto.keks import KeyRing
from classbeat.design import chat, corrections, diff, music_kind, revision
from classbeat.design.games_policy import has_retired_game
from classbeat.design.guidance import rule_set_for
from classbeat.design.interpreted import with_interpretation
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun
from classbeat.songs import revise_reading
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import ReviseSongsInput
from classbeat.songs.phase2 import final_checks, final_music, mark_mowl, music_source
from classbeat.songs.pick import stage_config

ORIGIN: Final = "revise_songs"

# The current pointer and the class's music move together, from the base only.
# A new current draft is a new song list, so a selection approved for the previous list is void.
_CAS = text(
    "UPDATE classes c SET current_revision_id = :revision_id, "
    "music_source = CAST(:music_source AS jsonb), approved_swedebeat_match_id = NULL, "
    "updated_at = now() FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id = :base RETURNING c.id"
)


async def run_revise_phase_two(
    run: JobRun,
    given: ReviseSongsInput,
    *,
    keyring: KeyRing,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
    context_requests: int,
) -> Complete:
    if given.base_revision_hash is None or given.request_message_id is None:
        raise JobFailure("internal")
    base_id, message_id = given.base_revision_id, given.request_message_id
    base_hash = bytes.fromhex(given.base_revision_hash)
    p, final, step = await final_music(run, keyring, given, config, base_revision_id=base_id)
    handed_over = False  # the outcome's ``release`` closes the step from then on
    try:
        async with run.fenced() as fs:
            base = await chat.load_base(
                fs.db, fs.lease.owner_id, fs.lease.job_id, base_id, context_requests
            )
            request = await chat.request_body(fs.db, fs.lease.owner_id, p.class_id, message_id)
        if base is None or base.kind != "draft" or not base.brief or request is None:
            raise JobFailure("invalid_input")
        if (
            base.content_hash != base_hash
            or hashlib.sha256(revision.canonical(base.content)).digest() != base_hash
        ):
            raise JobFailure("internal")  # a revision is immutable: the stored hash must hold
        try:
            brief = Brief.model_validate(base.brief)
            if brief.sport != "cycling":
                raise JobFailure("invalid_input")  # ADR 0018: running has no song picking
            base_draft = stored_draft(base.content, brief.sport)
        except ValidationError:
            raise JobFailure("internal") from None
        if has_retired_game(base.content):
            raise JobFailure("invalid_input")  # defence in depth: admit refuses ``game_retired``
        rules = rule_set_for(brief, base.dismissed_rules)
        await corrections.attach(run, step)  # the class's beat-one corrections (ADR 0007)
        stage = stage_config(
            agent, config, turns=agent.limits.max_turns, calls=agent.limits.max_tool_calls
        )
        stated = await revise_reading.phase_two(  # #1023: the request's values, as a revise
            run, given, provider, stage, brief, request, p.stored.provenance
        )
        ctx = ReviseContext.build(
            stated.brief,
            step.data,
            base_draft,
            base.earlier_requests,
            request,
            music_changed=True,
            songs_changed=True,
            rules=rules,
            gates=given.plan_gates,  # the plan's gates at creation (#496)
        )
        outcome = await run_stage(
            JobStageHost(run), provider, revise_stage(ctx), ctx.first_message(), stage
        )
        if not isinstance(outcome.output, ReviseResult):
            raise JobFailure("internal")
        revised = outcome.output
        draft = revision.build(
            revised.result,
            outcome,
            ctx.map,
            step,
            origin=ORIGIN,
            base_revision_id=base_id,
            base_hash=base_hash,
            request_message_id=message_id,
            song_list_id=p.revision_id,
            rules=rules.provenance() if rules else None,
        )
        draft = with_interpretation(
            draft,
            stated.brief,
            revised.result.unmet,
            given.plan_gates,
            extra=stated.items,
            stated=bool(brief.explicit),
        )
        change = diff.summarize(base.content, draft.content)
        mowl_playlist_id = step.data.playlist_id
        source = json.dumps(music_source(final), sort_keys=True, ensure_ascii=False)

        async def store(fs: FencedSession) -> UUID:
            """``final_checks``, the revision, the compare-and-set, the MOWL id, the message."""
            await final_checks(fs, p, final, step)
            revision_id = await draft.store(fs)
            # Never over a ``none`` source (#530): checked before the compare-and-set writes it.
            await music_kind.check_stored(fs, soundless=False)
            params = {
                "revision_id": revision_id,
                "music_source": source,
                "job_id": fs.lease.job_id,
                "owner_id": fs.lease.owner_id,
                "base": base_id,
            }
            await fs.db.execute(_CAS, params)  # no row: kept, but not current
            await mark_mowl(fs, p, final, mowl_playlist_id)
            try:
                await chat.insert_message(
                    fs.db,
                    owner_id=fs.lease.owner_id,
                    class_id=p.class_id,
                    job_id=fs.lease.job_id,
                    role="assistant",
                    body=revised.note,
                    base_revision_id=base_id,
                    result_revision_id=revision_id,
                    change=change,
                )
            except chat.MessageRefused:
                raise JobFailure("internal") from None
            return revision_id

        complete = Complete(store, release=step.close)
        handed_over = True
        return complete
    finally:
        if not handed_over:
            step.close()
