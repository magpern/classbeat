"""A built revision with how the request was read attached (ADR 0029 section 4).

Glue between ``request_policy`` (the pure reading) and ``revision.DraftRevision`` (what is
stored): the ``provenance.interpretation`` items are added to the revision's provenance, bounded
by ``interpretation_record``. A brief that states nothing and has no games gets its revision back
unchanged, so every revision of today is stored byte-identically.
"""

from collections.abc import Iterable
from dataclasses import fields

from classbeat.agent import interpretation_record
from classbeat.agent.brief import Brief
from classbeat.agent.explicit_check import Miss
from classbeat.design import request_policy
from classbeat.design.revision import DraftRevision
from classbeat.plan_gates import PlanGates


def with_interpretation(
    draft: DraftRevision, brief: Brief, unmet: Iterable[Miss], gates: PlanGates | None
) -> DraftRevision:
    items = request_policy.interpretation_for(brief, unmet, request_policy.Limits.from_gates(gates))
    if not items:
        return draft
    kept = {f.name: getattr(draft, f.name) for f in fields(draft) if f.name != "provenance"}
    provenance = interpretation_record.attach(draft.provenance, items)
    return DraftRevision(**kept, provenance=provenance)  # by name: a new field cannot be dropped
