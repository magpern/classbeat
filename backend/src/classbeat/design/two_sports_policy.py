"""A request for both sports at once (#1544, owner 2026-11-26: "Cycling and running in same prompt
rejects politely"; ADR 0018 Amendment 6; pure: no database, no network, no model).

A class is either cycling or running. The interpreter marks a request that asks for both
(``InterpretedRequest.two_sports``, a closed flag: "a ride, then a 20 min run"); the other sport
named only as context ("a cycling class for triathletes who run") is no such request and is read
as usual.

Such a reading ends here, before any design: ``ended_items`` stores one Not as asked ``sport``
item with the reason ``TWO_SPORTS`` (and no other ``sport`` item), the reading asks no question,
and the interpret job stores the refusal ``TWO_SPORTS`` with it. The hand-off (``classes``'
``design_input``) answers ``TWO_SPORTS`` for such a reading (``ended``) before anything else, a
retry included, so no design job is created and no class credit is taken. The page says that a
class is one sport and offers to change the request.
"""

from collections.abc import Iterable
from typing import Final

from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.design.request_policy import ALL_SPORTS

TWO_SPORTS: Final = "two_sports"
SPORT: Final = "sport"


def asked_both(read: InterpretedRequest) -> bool:
    """Whether the interpreter marked the request as asking for both sports (only ``True``)."""
    return read.two_sports is True


def ended_items(items: Iterable[InterpretationItem]) -> list[InterpretationItem]:
    """The reading's items with its ``sport`` item replaced by the Not as asked ``TWO_SPORTS``
    one (first), whose value is both sports: what was asked for."""
    kept = [i for i in items if i.path != SPORT]
    refused = InterpretationItem(
        path=SPORT, origin="not_as_asked", reason_code=TWO_SPORTS, value=list(ALL_SPORTS)
    )
    return [refused, *kept]


def ended(items: Iterable[InterpretationItem]) -> bool:
    """Whether a stored reading was ended for asking for both sports."""
    return any(i.path == SPORT and i.reason_code == TWO_SPORTS for i in items)
