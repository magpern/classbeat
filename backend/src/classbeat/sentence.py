"""A server sentence as a code, its numbers and today's English text (ADR 0023 section 6).

The page words ``code`` from its catalogue (``sentence.<code>``) and shows ``text`` for a code it
does not know. ``params`` holds only whole numbers; ``count`` is the one the page's plural rules
read (a number of minutes or hours). Never a provider's text, a token or a link."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sentence:
    code: str
    text: str
    params: dict[str, int] = field(default_factory=dict)
