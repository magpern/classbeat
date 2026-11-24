"""Where a reported value came from (ADR 0029 section 4; pure): the base of every stated field of
an ``InterpretedRequest``, split out of ``interpreted`` (and the taste and shape fields with it).

``origin`` is ``explicit`` (the instructor said it) or ``inferred``; ``quote`` is a convenience for
the page and is kept only if it is a verbatim substring of the request (``with_quote``).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from classbeat.agent.interpretation_record import MAX_QUOTE, keep_quote
from classbeat.agent.schemas import STRICT

StatedOrigin = Literal["explicit", "inferred"]
Quote = Annotated[str, Field(min_length=1, max_length=MAX_QUOTE)]


class Stated(BaseModel):
    """Where a value came from: ``explicit`` (the instructor said so) or ``inferred``."""

    model_config = STRICT

    origin: StatedOrigin
    quote: Quote | None = None


def with_quote[T: Stated](held: T, request: str) -> T:
    kept = keep_quote(held.quote, request)
    return held if kept == held.quote else held.model_copy(update={"quote": kept})
