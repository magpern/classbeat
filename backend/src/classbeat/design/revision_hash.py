"""The one ``content_hash`` of a draft revision (ADR 0007 section 2, ADR 0036 section 1 and B10, ADR
0042 section 3, #1391). Pure: no I/O.

``revision_hash`` is the **one** function every place that stores a draft or recomputes and compares
a stored hash calls (``design.revision``'s write, ``saving.approval``, ``swedebeat_studio
.current_draft``, ``swedebeat_studio.revise_kind``, ``swedebeat_studio.mix_proposal``, the user-edit
write ``classes.user_edit``). It is chosen by whether the revision's ``edit_overlay`` is NULL:

- **NULL** (every revision stored before #1391, and one whose overlay was cleared): exactly the hash
  of before, so no existing row, approval or saved program changes: the SHA-256 of the canonical
  ``content`` (a MOWL draft), or ``studio_hash`` of ``{content, mix_spec}`` (a studio draft);
- **an overlay**: ``edited_hash``, the SHA-256 of the canonical ``{content, mix_spec?, overlay}``
  (``mix_spec`` only for a studio draft), studio and MOWL drafts alike. So an approval, which binds
  the hash, binds the overlay too, and a lock-only or pin-only edit is a revision of its own.

The canonical JSON is ADR 0004 section 2's: sorted keys, no spaces, UTF-8. The two forms cannot
collide: a plain hash covers a ``WorkoutDraft`` (``name``, ``segments``) or ``{content, mix_spec}``,
an edited one always has the ``overlay`` key.
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def studio_hash(content: Mapping[str, Any], mix_spec: Mapping[str, Any]) -> bytes:
    """A studio draft's ``content_hash`` without an overlay (ADR 0036 section 1, amending ADR 0007
    section 2): the SHA-256 of the canonical JSON ``{"content": ..., "mix_spec": ...}``."""
    return hashlib.sha256(canonical({"content": content, "mix_spec": mix_spec})).digest()


def edited_hash(
    content: Mapping[str, Any], mix_spec: Mapping[str, Any] | None, overlay: Mapping[str, Any]
) -> bytes:
    """The ``content_hash`` of a draft with an edit overlay (module docstring)."""
    body: dict[str, Any] = {"content": content, "overlay": overlay}
    if mix_spec is not None:
        body["mix_spec"] = mix_spec
    return hashlib.sha256(canonical(body)).digest()


def revision_hash(
    content: Mapping[str, Any],
    *,
    mix_spec: Mapping[str, Any] | None = None,
    overlay: Mapping[str, Any] | None = None,
) -> bytes:
    """The one hash of a draft (module docstring): ``mix_spec`` for a studio draft, ``overlay``
    the stored ``edit_overlay`` (``None`` for NULL)."""
    if overlay is not None:
        return edited_hash(content, mix_spec, overlay)
    if mix_spec is not None:
        return studio_hash(content, mix_spec)
    return hashlib.sha256(canonical(content)).digest()


STUDIO_SOURCE = "swedebeat_studio"  # ``swedebeat_studio.song_list_model.SOURCE`` (no import: pure)


def of_music(
    content: Mapping[str, Any], music: Mapping[str, Any], overlay: Mapping[str, Any] | None
) -> bytes:
    """``revision_hash`` of a revision about to be stored, its ``music`` already dumped
    (``mode="json"``): the ``mix_spec`` is the studio draft's, else none."""
    spec = music.get("mix_spec") if music.get("source") == STUDIO_SOURCE else None
    return revision_hash(content, mix_spec=spec, overlay=overlay)
