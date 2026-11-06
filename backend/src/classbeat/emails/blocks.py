"""The block content model (ADR 0022 section 2): what a mail says, with no markup.

One layout (``layout.py``) renders the same blocks to HTML and text, so both parts carry the same
words and links, and every string is escaped in one place.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Heading:
    text: str


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str


@dataclass(frozen=True, slots=True)
class Button:
    """A call to action. ``primary=False`` renders as a plain link line."""

    label: str
    url: str
    primary: bool = True


@dataclass(frozen=True, slots=True)
class Note:
    """A set-off remark. ``contact`` ends it with the support address (a ``mailto:`` link)."""

    text: str
    contact: bool = False


@dataclass(frozen=True, slots=True)
class Bullets:
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EventTime:
    """When something happened, in UTC (``label`` is catalogue text)."""

    label: str
    when: datetime


Block = Heading | Paragraph | Button | Note | Bullets | EventTime


@dataclass(frozen=True, slots=True)
class Content:
    """One mail before layout: subject, the hidden preheader and the blocks."""

    subject: str
    preheader: str
    blocks: tuple[Block, ...]
    # The catalogue sentence for "why you got this" in the footer.
    reason: str
