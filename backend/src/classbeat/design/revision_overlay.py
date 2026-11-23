"""The edit overlay of a revised, re-picked or refitted MOWL draft, read and stored (ADR 0042
sections 3 and 4, #1451). Used by ``revise_base`` (``revise_draft``), ``songs.revise_phase2``
(``revise_songs``) and ``refit`` (``refit_draft``); the rules are ``spotify_overlay_calc``.

- ``base_overlay``: the base's overlay once its **one hash** holds: the hash ``admit`` stored, the
  stored ``content_hash`` and ``revision_hash`` of the content **and** its ``edit_overlay`` must
  all be equal (a revision is immutable), so a tampered overlay is ``internal`` like a tampered
  content. An overlay that does not read is ``internal`` too: it fails closed, never dropped.
- ``only_spotify``: an overlay with records on anything but a cycling Spotify draft (the only
  MOWL draft the editor writes, #1424) is ``internal``: no path here would carry it.
- ``carried``: the draft to store with the reconciled overlay (``storable``: only as a read would
  accept it) and the one hash over both; a revise whose content and overlay are the base's again
  is ``invalid_output`` (``base_hash``; ``None`` for a refit, stored even when it is the same).
"""

from dataclasses import fields
from typing import Any, cast

from classbeat.design.edit_overlay_model import EditOverlay, parsed, storable
from classbeat.design.revision import DraftRevision
from classbeat.design.revision_hash import revision_hash
from classbeat.jobs import JobFailure


def base_overlay(
    content: Any, raw_overlay: object, stored_hash: bytes, base_hash: bytes
) -> EditOverlay:
    """The base's overlay (module docstring); ``JobFailure("internal")`` otherwise."""
    if not isinstance(content, dict) or (
        raw_overlay is not None and not isinstance(raw_overlay, dict)
    ):
        raise JobFailure("internal")
    overlay = cast(dict[str, Any] | None, raw_overlay)
    hashed = revision_hash(cast(dict[str, Any], content), overlay=overlay)
    if stored_hash != base_hash or hashed != base_hash:
        raise JobFailure("internal")  # a revision is immutable: the stored hash must hold
    try:
        return parsed(raw_overlay)
    except ValueError:
        raise JobFailure("internal") from None


def only_spotify(overlay: EditOverlay, spotify_cycling: bool) -> None:
    """``internal`` for records on a base the editor never writes (module docstring)."""
    if overlay.records and not spotify_cycling:
        raise JobFailure("internal")


def carried(draft: DraftRevision, overlay: object, base_hash: bytes | None) -> DraftRevision:
    """``draft`` with ``overlay`` (an ``EditOverlay``, or ``None`` for none) and its one hash."""
    if overlay is None:
        return draft
    if not isinstance(overlay, EditOverlay):
        raise JobFailure("internal")
    try:
        stored = storable(overlay)
    except ValueError:
        raise JobFailure("internal") from None
    content_hash = revision_hash(draft.content, overlay=stored)
    if base_hash is not None and content_hash == base_hash:
        raise JobFailure("invalid_output")  # the base again: not a revision
    swapped = ("content_hash", "edit_overlay")
    kept = {f.name: getattr(draft, f.name) for f in fields(draft) if f.name not in swapped}
    return DraftRevision(**kept, content_hash=content_hash, edit_overlay=stored)  # by name
