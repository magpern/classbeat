"""What the design stages are told about the brief's ride shape (ADR 0029 section 4 amendment of
2026-11-24, #1493). Pure text, shared by the cycling ``design`` and ``revise`` stages, the
soundless pair and (through ``revise``) the composer's design with songs.

The text names no music (the soundless stages carry it too) and no model-written text: the brief's
``shape`` is closed values. ``agent.shape_check`` is what holds a draft to it.
"""

from typing import Final

SHAPE_RULES: Final = """\
The ride shape: when the brief has a "shape", it is the order of the ride in parts, as the \
instructor described it, each part with a kind and the values they gave. Design the class to \
follow it:
- Tag every segment with "section": the index, from 0, of the part it belongs to. A part is one \
or more segments, every part has at least one, and the tags never go down through the draft. A \
warm-up first and a cooldown last that no part names have no tag: add them when the shape has \
none.
- A climb part with a count: one "main" segment per hill, their number inside the count's min \
and max.
- An efforts part: its efforts are short hard intervals, zone 5 or more and at most 90 s each \
after fitting, their number inside the count's min and max, seated or standing as the load \
suggests (heavy may be standing).
- A part that is rising climbs in zone as it goes; one that is falling eases.
- Where the shape leaves a choice (a number inside a range, a length), you choose.
Without a shape, give no "section".
"""

SHAPE_REVISE: Final = """\
When the brief has a shape, the base draft's segments carry their section tags: keep them and tag \
every segment you add. The brief's shape is the class's shape after this request, so where the \
request changed it (a number of efforts, a part added or taken out), follow the new shape and \
tag the segments to match.
"""

SHAPE_REVISE_RULES: Final = SHAPE_RULES + SHAPE_REVISE
