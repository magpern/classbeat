"""The stated part of a brief that a revise leaves behind (ADR 0029 section 4, #904; pure).

A revise layers its request's stated values onto the brief it starts from
(``classbeat.design.revise_layer_policy``), but ``classes.brief`` stays as the first design stored
it. So the revision records the layered values in ``provenance.stated`` (``StatedLayer``): the
brief's closed knobs, its games and its explicit paths, never its text or style. The next revise,
and a Change songs, start from them: ``classbeat.design.chat.load_base`` applies the layer nearest
the base along its ``parent_id`` chain onto the class's brief (``applied``), so a refit or a song
change in between, which records no layer, keeps the one before it, and picking an older revision
picks its values too.

A layer holds closed numbers and enums only (as the brief does), so no model text reaches it.
"""

from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

from classbeat.agent.brief import MAX_EXPLICIT, MAX_GAMES, Brief, BriefGame, ExplicitPath
from classbeat.agent.schemas import STRICT

# The brief's fields a layer replaces as a whole; every other field stays the class's own.
LAYER_KEYS: Final = (
    "length_min",
    "intensity",
    "climbs",
    "sprints",
    "races",
    "ftp_test",
    "class_type",
    "games",
    "explicit",
)


class StatedLayer(BaseModel):
    """``provenance.stated``: the knobs a running brief has not are absent."""

    model_config = STRICT

    length_min: int = Field(ge=10, le=180)
    intensity: Literal["easy", "moderate", "hard"] = "moderate"
    climbs: int | None = Field(default=None, ge=0, le=10)
    sprints: int | None = Field(default=None, ge=0, le=20)
    races: int | None = Field(default=None, ge=0, le=5)
    ftp_test: Literal[3, 5, 20] | None = None
    class_type: Literal["plain", "interval", "hiit", "journey", "ftp_test"] | None = None
    games: list[BriefGame] = Field(default_factory=lambda: list[BriefGame](), max_length=MAX_GAMES)
    explicit: list[ExplicitPath] = Field(
        default_factory=lambda: list[ExplicitPath](), max_length=MAX_EXPLICIT
    )


def layer_of(brief: Brief) -> dict[str, Any]:
    """The layer of a layered brief, as stored (an empty ``games`` or ``explicit`` left out)."""
    dumped = brief.model_dump(mode="json", exclude_none=True)
    layer = StatedLayer.model_validate({k: dumped[k] for k in LAYER_KEYS if k in dumped})
    out = layer.model_dump(mode="json", exclude_none=True)
    for key in ("games", "explicit"):
        if not out.get(key):
            out.pop(key, None)
    return out


def applied(brief: Mapping[str, Any], layer: Mapping[str, Any] | None) -> dict[str, Any]:
    """The class's stored brief with the layer's fields in place of its own (``LAYER_KEYS``: a
    field the layer leaves out takes the brief's default). No layer: the brief as it is. The
    caller validates the result as a ``Brief``, so a layer that no longer checks fails there."""
    if layer is None:
        return dict(brief)
    kept = {k: v for k, v in brief.items() if k not in LAYER_KEYS}
    return kept | {k: v for k, v in layer.items() if k in LAYER_KEYS}
