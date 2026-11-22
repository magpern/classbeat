"""Whether a base revision is a Spotify draft the user edited (ADR 0042 section 3, #1424). Pure.

An AI revise, a song revise and a refit of an edited Spotify draft would have to carry the user's
overlay (re-apply its overrides, enforce its locks) as a studio revise does. That is not built for
a Spotify draft yet, so these three are refused ``has_user_edits`` while the base holds the user's
work, and nothing is written: a silent drop of the edits is never an option. A studio draft keeps
its own path (``swedebeat_studio.revise_kind``). The way forward the user is given is to undo the
edits (Undo, or make an earlier version current) and ask again.
"""

from typing import Any, Final

REASON: Final = "has_user_edits"
SPOTIFY: Final = "mowl_spotify_playlist"
USER_EDIT: Final = "user_edit"


def is_edited_spotify(source: object, origin: object, overlay: object) -> bool:
    """A Spotify draft that is a ``user_edit`` revision or whose overlay holds any record."""
    if source != SPOTIFY:
        return False
    if origin == USER_EDIT:
        return True
    records: Any = overlay.get("records") if isinstance(overlay, dict) else None
    return isinstance(records, list) and len(records) > 0  # pyright: ignore[reportUnknownArgumentType]
