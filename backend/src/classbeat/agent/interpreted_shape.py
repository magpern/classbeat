"""The shape of the ride as the interpreter reports it (ADR 0029 section 4 amendment, #1485; pure).

``InterpretedRequest.shape``: the sections of the request in the order it gives them, each the
section fields of ``ride_shape`` (closed values and numbers) with an ``origin`` and an optional
``quote`` (kept only if verbatim). Only when the request describes the ride in parts.
"""

from classbeat.agent.ride_shape import SectionFields, ShapeSection
from classbeat.agent.stated import Stated


class InterpretedSection(SectionFields, Stated):
    def closed(self) -> ShapeSection:
        """The brief's section: the fields without the origin and the quote."""
        return ShapeSection.model_validate(self.model_dump(exclude={"origin", "quote"}))
