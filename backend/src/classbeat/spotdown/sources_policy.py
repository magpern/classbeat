"""``CLASSBEAT_SPOTDOWN_SOURCES``: which spotdown sources classbeat may use (ADR 0040 sections 4
and 10). Pure: no I/O.

A comma list of ``upload`` (classbeat streams bytes it already downloaded under its own caps:
SwedeBeat tracks, the mix, user audio) and ``youtube`` (spotdown matches and fetches; classbeat
sends only metadata). Empty, the default, is no source: every client call that needs one is
refused before anything is sent. spotdown's ``url`` source is **never** classbeat's: a MOWL
temporary link never leaves classbeat (ADR 0040 section 10), so ``url`` is refused here like any
unknown name. ``youtube`` is also off in spotdown by default and may be enabled only after the
owner's decision #1296 (ADR 0040 section 12); this setting cannot enable it there.

A refusal names the rule, never the value, and the process refuses to start.
"""

from typing import Final, Literal

Source = Literal["upload", "youtube"]
SOURCES: Final[frozenset[str]] = frozenset({"upload", "youtube"})
_NAME: Final = "spotdown_sources"


class SpotdownSourcesRefused(ValueError):
    """``CLASSBEAT_SPOTDOWN_SOURCES`` breaks a rule; the message names it, never the value."""

    def __init__(self) -> None:
        super().__init__(f"{_NAME} must be a comma list of upload and youtube")


def parse_sources(value: str) -> frozenset[str]:
    """The sources of a checked value (``frozenset()`` for an empty one)."""
    names = [part.strip() for part in value.split(",")] if value.strip() else []
    if any(name not in SOURCES for name in names) or len(names) != len(set(names)):
        raise SpotdownSourcesRefused
    return frozenset(names)


def canonical(value: str) -> str:
    """The setting's validator: the sources, sorted and comma-joined (``""`` for none)."""
    return ",".join(sorted(parse_sources(value)))
