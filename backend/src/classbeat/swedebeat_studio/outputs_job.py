"""What one ``produce_outputs`` run holds, and the one way its steps write (ADR 0036 section 6,
ADR 0005 section 7; SR C7).

``Producer`` is the run's context: the job, the approval (``given``) re-checked at the start, the
approved draft (``draft``) with its list's SwedeBeat facts, the user's analyses of its tracks,
the SwedeBeat bearer and the gateway's client key, and the class title.

``write(request, call)`` is every gateway write: the ADR 0005 protocol (``operations.perform``:
the write intent committed first, outside any transaction the call, then its outcome) whose
**guard**, in the decide transaction, share-locks the approved SwedeBeat connection at the
approved generation (``connection_changed`` otherwise) and re-reads the class's current draft
with the approved hash, share-locked (``invalid_input`` otherwise), so no write goes out for
another account or another draft. The call's ``WriteFailure`` maps to the protocol: a definite
refusal or a request that never left is ``NotWritten`` (``failed``, safe to retry) with its
category (``writes_off`` for the gateway's ``403``, a normal state the user sees); anything that
may have been written is ``uncertain`` (``in_doubt``: never sent again by a job). A refused
bearer is confirmed with one session check (``swedebeat_match.gateway_match.call_failure``).
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.crypto.keks import KeyRing
from classbeat.design.track_cache import TrackMap
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations import CallResult, NotWritten, UncertainAnswer, perform
from classbeat.operations.requests import Guard, OperationRequest
from classbeat.spotdown.studio_map_model import stored_timing_class
from classbeat.studio.mix_facts_model import TrackFacts, of_map
from classbeat.studio.output_body_policy import Facts
from classbeat.swedebeat_gateway.writes import WriteFailure
from classbeat.swedebeat_match.gateway_match import call_failure
from classbeat.swedebeat_studio.current_draft import CurrentDraft
from classbeat.swedebeat_studio.outputs_admit import held, the_draft
from classbeat.swedebeat_studio.outputs_input import ProduceOutputsInput

# A write's definite refusals and the job category each fails with; ``bearer_refused`` is
# confirmed first. ``upstream_unavailable`` is a request that provably never left (retried).
_REFUSED: Final = {
    "app_misconfigured": "app_misconfigured",
    "bearer_refused": "needs_reconnect",
    "writes_off": "writes_off",
    "rate_limited": "rate_limited",
    "invalid_input": "invalid_input",
    "track_unavailable": "track_unavailable",
    "track_too_large": "track_too_large",
    "upstream_unavailable": "upstream_unavailable",
    "invalid_response": "invalid_response",
}


@dataclass(frozen=True)
class Producer:
    run: JobRun
    keyring: KeyRing
    given: ProduceOutputsInput
    draft: CurrentDraft
    bearer: SwedebeatBearer
    key: SecretStr
    title: str | None
    maps: Mapping[str, TrackMap]

    @property
    def owner_id(self) -> UUID:
        return self.run.lease.owner_id

    @property
    def class_id(self) -> UUID:
        return self.given.the_class

    @property
    def references(self) -> list[str]:
        return self.draft.music.mix_spec.references()

    def facts(self) -> dict[str, Facts]:
        found = {
            s.swedebeat.reference: Facts(s.swedebeat.title, s.swedebeat.artist, s.swedebeat.bpm)
            for s in self.draft.songs.content.songs
            if s.swedebeat is not None and not s.gap
        }
        if any(r not in found for r in self.references):
            raise JobFailure("internal")  # each piece of the mix is a track of the approved list
        return found

    def durations(self) -> dict[str, float]:
        return {r: float(self.maps[r].music_map["duration"]) for r in self.references}

    def track_facts(self) -> dict[str, TrackFacts]:
        """Each piece's stored analysis as the mix rules read it (the render's curves, #1330)."""
        out: dict[str, TrackFacts] = {}
        for r in self.references:
            found = self.maps[r]
            spotdown = found.analyser == "spotdown"
            timing = stored_timing_class(found.provenance) if spotdown else None
            out[r] = of_map(found.music_map, timing)
        return out

    def sha256s(self) -> dict[str, str]:
        return {r: self.maps[r].sha256 for r in self.references}

    def still_approved(self) -> Guard:
        """The decide step's guard (see the module docstring)."""
        given, run = self.given, self.run
        generation = given.swedebeat_generation

        async def guard(db: AsyncSession) -> bool:
            if generation is None:
                raise JobFailure("internal")
            if not await held(db, run.ctx, given.swedebeat_connection_id, generation):
                raise JobFailure("connection_changed")
            found = await the_draft(db, run.lease.owner_id, given, lock=True)
            if isinstance(found, str):
                raise JobFailure("invalid_input")
            return True

        return guard

    async def write(
        self, request: OperationRequest, call: Callable[[], Awaitable[CallResult]]
    ) -> str:
        """One gateway write (see the module docstring); its external id."""

        async def translated() -> CallResult:
            try:
                return await call()
            except WriteFailure as failed:
                outcome = failed
            if outcome.sent:
                raise UncertainAnswer
            raise NotWritten(_REFUSED.get(outcome.category, "invalid_response"))

        try:
            done = await perform(self.run, request, translated, keyring=self.keyring)
        except JobFailure as failure:
            if failure.category != "needs_reconnect":
                raise
            confirmed = await call_failure(self.run, self.bearer, self.key, "bearer_refused", None)
            raise confirmed from None
        return done.external_id


def result_of(external_id: str, **result: Any) -> CallResult:
    return CallResult(external_id, result)
