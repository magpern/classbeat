"""A Spotify draft's edit overlay through an AI revise, a song revise and a refit (ADR 0042 section
3, "On AI regeneration", #1451). Pure: no I/O.

The instructor's edits of a Spotify draft (#1424) are carried by the same rules as a studio revise
(``swedebeat_studio.revise_overlay``), less what a Spotify draft cannot have: it has no mix, so no
cut pin, and its songs play whole, so no anchor (the edit refuses both, ``audio_edit_unavailable``).

- ``kept``: for a revise or a song revise, the overlay's locks and overrides stated as fixed in the
  model's message (``revise_overlay.fixed_section``), and each draft the model submits reconciled
  with them in the fixed order (``revise_overlay.kept_on``: locks checked, boundary overrides, then
  attribute overrides re-applied, ``agent.design.check`` on the run's map and the plan gates, a
  timing-locked block the fit moved refused). A lock the model broke is ``overlay_violated``, a
  correction round, so the user's edits are never dropped silently; a draft that never keeps them
  ends the job ``invalid_output`` with nothing stored and the base still current. An override
  survives with no lock, its ``ai_value_at_set`` the model's new value.
- ``refitted``: for a refit (no model), the same reconciliation of the base fitted again to the
  corrected map; any refusal there is the caller's ``invalid_output``.
- ``records``: the records carried. A cut pin or an anchor on a Spotify draft is not one the edit
  wrote, so it is ``ValueError`` and the caller fails closed (``internal``), never dropped.

The reconciled overlay names blocks by segment kind and class time only, no song, so a revision
stores no Spotify id, title or artist in it (ADR 0006 section 6).
"""

from typing import Any

from classbeat.agent.design import DesignResult
from classbeat.agent.revise import Kept
from classbeat.design.edit_overlay_model import Anchor, EditOverlay, Event, OverlayPin, Record
from classbeat.plan_gates import PlanGates
from classbeat.swedebeat_studio.revise_overlay import fixed_section, kept_on
from classbeat.swedebeat_studio.studio_edit_calc import fitted_content


def records(overlay: EditOverlay) -> tuple[Record, ...]:
    """The overlay's records; ``ValueError`` for a cut pin or an anchor (module docstring)."""
    if any(isinstance(r, OverlayPin | Anchor) for r in overlay.records):
        raise ValueError("a Spotify draft has no cut pin and no anchor")
    return tuple(overlay.records)


def _no_event(event: Event) -> int | None:
    return None  # never reached: ``records`` holds no anchor


def _reconciled(
    result: DesignResult,
    base: dict[str, Any],
    found: tuple[Record, ...],
    music_map: dict[str, Any],
    gates: PlanGates | None,
) -> tuple[DesignResult, EditOverlay]:
    content = fitted_content(result.draft.model_dump(mode="json"), result.spec)
    return kept_on(content, base, found, (), music_map, gates, _no_event)


def kept(base: dict[str, Any], overlay: EditOverlay, gates: PlanGates | None) -> Kept | None:
    """The revise stage's ``Kept`` for ``base`` (the base draft as the model sees it) and its
    overlay; ``None`` with no record (a plain base, or one whose edits were all cleared)."""
    found = records(overlay)
    if not found:
        return None

    def reconcile(result: DesignResult, music_map: dict[str, Any]) -> tuple[DesignResult, Any]:
        return _reconciled(result, base, found, music_map, gates)

    return Kept(fixed_section(base, overlay), reconcile)


def refitted(
    result: DesignResult, base: dict[str, Any], overlay: EditOverlay, music_map: dict[str, Any]
) -> tuple[DesignResult, EditOverlay | None]:
    """A refit's ``result`` with the overlay re-applied (module docstring): the result to store
    and its overlay (``None`` with no record: ``result`` as it is). ``ToolError`` refuses."""
    found = records(overlay)
    if not found:
        return result, None
    return _reconciled(result, base, found, music_map, None)
