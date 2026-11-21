"""The repeats of a mix spec (ADR 0036 amendment "a song may appear more than once in a mix",
#1342). Pure: no I/O.

Terms of the amendment: a **piece** is one entry of the spec's ``tracks``; a **repeat** is a piece
whose reference appeared in an earlier piece; a **loop join** is a change of piece between two
pieces of the **same** reference; a **loop run** is a maximal run of adjacent pieces of one
reference (one piece or more).

Everything here takes the pieces' references in mix order (``MixSpec.references()``) and returns
positions or references, never a model's word:

- ``has_repeats``: any reference plays in more than one piece (A8: the class is mix-only);
- ``first_order``: each reference once, in the order of its first piece (A8: the final Spotify
  playlist);
- ``loop_joins``: each ``n`` whose change of piece (from piece ``n`` to ``n + 1``) is a loop join;
- ``runs``: the loop runs as ``(first, last)`` positions, in mix order (A8: X-Tracks);
- ``repeat_of``: per piece, the position of the first piece of its reference when it is a repeat,
  else ``None`` (A8: the timeline's marks).
"""

from collections.abc import Sequence


def has_repeats(references: Sequence[str]) -> bool:
    return len(set(references)) != len(references)


def first_order(references: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(references))


def loop_joins(references: Sequence[str]) -> list[int]:
    return [n for n in range(len(references) - 1) if references[n] == references[n + 1]]


def runs(references: Sequence[str]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for n, reference in enumerate(references):
        if out and references[out[-1][1]] == reference:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def repeat_of(references: Sequence[str]) -> list[int | None]:
    first: dict[str, int] = {}
    out: list[int | None] = []
    for n, reference in enumerate(references):
        out.append(first.get(reference))
        first.setdefault(reference, n)
    return out
