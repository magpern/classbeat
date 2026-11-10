"""A built revision with how the request was read attached (ADR 0029 section 4).

Glue between ``request_policy`` (the pure reading) and ``revision.DraftRevision`` (what is
stored): the ``provenance.interpretation`` items are added to the revision's provenance, bounded
by ``interpretation_record``. A brief that states nothing and has no games gets its revision back
unchanged, so every revision of today is stored byte-identically.

A revise that layered its request's stated values (``revise_stated``, #904) also passes the
layer's Not as asked items (``extra``: each one replaces the item of its path) and ``stated``:
the revision then records ``provenance.stated`` (``agent.stated_layer``), so the next revise
starts from the layered values.
"""

from collections.abc import Iterable
from dataclasses import fields

from classbeat.agent import interpretation_record
from classbeat.agent.brief import Brief
from classbeat.agent.explicit_check import Miss
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.stated_layer import layer_of
from classbeat.design import request_policy
from classbeat.design.revision import DraftRevision
from classbeat.plan_gates import PlanGates


def with_interpretation(
    draft: DraftRevision,
    brief: Brief,
    unmet: Iterable[Miss],
    gates: PlanGates | None,
    *,
    extra: Iterable[InterpretationItem] = (),
    stated: bool = False,
) -> DraftRevision:
    items = request_policy.interpretation_for(brief, unmet, request_policy.Limits.from_gates(gates))
    added = list(extra)
    if added:
        replaced = {i.path for i in added}
        items = [i for i in items if i.path not in replaced] + added
    if not items and not stated:
        return draft
    kept = {f.name: getattr(draft, f.name) for f in fields(draft) if f.name != "provenance"}
    provenance = interpretation_record.attach(draft.provenance, items)
    if stated:
        provenance = {**provenance, "stated": layer_of(brief)}
    return DraftRevision(**kept, provenance=provenance)  # by name: a new field cannot be dropped
