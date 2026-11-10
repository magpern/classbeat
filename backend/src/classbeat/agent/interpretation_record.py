"""How a request was read: the ``provenance.interpretation`` items (ADR 0029 section 4; pure).

An item is ``{path, origin, reason_code?, value, quote?}``:

- ``origin``: ``explicit`` (the instructor stated it: "As you asked"), ``inferred`` or ``default``
  (the system chose: "Assumed"), or ``not_as_asked`` (the instructor asked and it was not done,
  with a ``reason_code``: "Not as asked");
- ``value``: the requested value for an explicit or not-as-asked item, the chosen one otherwise;
  small closed JSON (numbers, short enum strings, a game as a small object);
- ``quote``: words of the request, **kept only if they are a substring of ``brief.text``**
  (``keep_quote``), so text a model made up never reaches a stored record or a later prompt.

The list is bounded here, so a revision's provenance cannot grow without limit.
"""

import json
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

Origin = Literal["explicit", "inferred", "default", "not_as_asked"]
Section = Literal["as_asked", "not_as_asked", "assumed"]

MAX_ITEMS: Final = 40
MAX_VALUE_BYTES: Final = 300
MAX_QUOTE: Final = 200
# A path of the brief ("length_min", "games.0", "games.0.duration_sec", "music").
PATH: Final = r"^[a-z][a-z_]{0,29}(\.[0-9]{1,2})?(\.[a-z_]{1,30})?$"


class InterpretationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(pattern=PATH)
    origin: Origin
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    value: JsonValue = None
    quote: str | None = Field(default=None, min_length=1, max_length=MAX_QUOTE)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.origin == "not_as_asked") != (self.reason_code is not None):
            raise ValueError("a not-as-asked item has a reason code, and only it")
        size = len(json.dumps(self.value, ensure_ascii=False, separators=(",", ":")).encode())
        if size > MAX_VALUE_BYTES:
            raise ValueError("the value is too long")
        return self

    def section(self) -> Section:
        if self.origin == "explicit":
            return "as_asked"
        return "not_as_asked" if self.origin == "not_as_asked" else "assumed"


InterpretationList = Annotated[list[InterpretationItem], Field(max_length=MAX_ITEMS)]
_LIST: Final = TypeAdapter[list[InterpretationItem]](InterpretationList)


def keep_quote(quote: str | None, text: str) -> str | None:
    """``quote`` when it is a non-empty, bounded substring of the request text, else ``None``."""
    if quote and len(quote) <= MAX_QUOTE and quote in text:
        return quote
    return None


def attach(provenance: dict[str, Any], items: list[InterpretationItem]) -> dict[str, Any]:
    """``provenance`` (a revision's, as dumped) with the bounded items; unchanged when there are
    none, so a revision without a stated request is stored exactly as before."""
    if not items:
        return provenance
    _LIST.validate_python(items)
    return {
        **provenance,
        "interpretation": [i.model_dump(mode="json", exclude_none=True) for i in items],
    }
