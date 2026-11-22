"""The overlay's cut pins applied to a derived mix spec, and the pins to listen to again (ADR 0042
section 5 and section 8, #1390). Pure: no I/O.

- ``records``: the overlay's pin records (``mix_pin_model.PinRecord``), each target
  ``{reference, occurrence, edge}`` at most once, else ``ValueError`` (fail closed).
- ``with_pins``: the spec with the records' pins written into its pieces (``start_pin`` /
  ``end_pin``, and the piece's cut set to the pin's proposed one); every pin the spec carried before
  is removed first, so the result is a function of the spec's cuts and the records only. A record
  applies to the ``occurrence``-th piece of its ``reference`` in mix order (0 for the first), by
  target, never by position, so it survives a new model spec. One that cannot apply is reported,
  kept and without effect:
  - **dropped**: its song is no longer on the approved list (replaced by another: a notice);
  - **unapplied**: its song is on the list but has no such piece, or was made **whole** by
    ``mix_choice_policy`` (``keep_whole``, ``trim`` off; a whole song has no cut).
  With no record that applies, the spec is returned with its cuts as they are and no pin key at
  all, so a draft without pins derives, dumps and hashes exactly as before. The result is not
  checked here: the caller runs ``mix_plan_policy.checked`` and the class-length rule on it
  (``mix_choice_policy.pinned``).
- ``listen_again``: the pins of a checked spec to listen to again before approval
  (``pin_unconfirmed``): a **confirmed** pin whose ``confirmed_against`` is not the SHA-256 of the
  analysed file the draft holds for that song (``music_changed``: a new file), or a song with no
  such record at all (fail closed). It never moves a cut: a confirmed cut stays ``confirmed_sec``
  until the user re-confirms or clears it. An unconfirmed pin is a request that follows the
  analysis it is checked on (a ``bar`` pin re-snaps on reanalysis) and needs no confirmation.
"""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from classbeat.studio.mix_pin_model import EDGES, FIELD_OF, PIN_FIELDS, CutPin, Edge, PinRecord


@dataclass(frozen=True, slots=True)
class Pinned:
    spec: dict[str, Any]
    unapplied: tuple[PinRecord, ...] = ()
    dropped: tuple[PinRecord, ...] = ()


def records(raw: Iterable[object]) -> tuple[PinRecord, ...]:
    """The overlay's pin records, each target once (module docstring)."""
    out = tuple(r if isinstance(r, PinRecord) else PinRecord.model_validate(r) for r in raw)
    if len({r.target() for r in out}) != len(out):
        raise ValueError("a cut is pinned once")
    return out


def occurrences(spec: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Each piece's ``(reference, occurrence)`` in mix order."""
    seen: Counter[str] = Counter()
    out: list[tuple[str, int]] = []
    for track in spec["tracks"]:
        reference = str(track["reference"])
        out.append((reference, seen[reference]))
        seen[reference] += 1
    return out


def _bare(track: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in track.items() if k not in PIN_FIELDS}


def with_pins(
    spec: Mapping[str, Any],
    pins: Iterable[PinRecord],
    *,
    approved: Collection[str | None] | None = None,
    whole: Collection[str] = (),
) -> Pinned:
    """``spec`` with ``pins`` applied by target (module docstring). ``approved``: the approved
    list's references (``None`` skips the dropped check); ``whole``: the references made whole."""
    found = records(pins)
    at = {target: n for n, target in enumerate(occurrences(spec))}
    tracks = [_bare(t) for t in spec["tracks"]]
    unapplied: list[PinRecord] = []
    dropped: list[PinRecord] = []
    for record in found:
        piece = at.get((record.reference, record.occurrence))
        if approved is not None and record.reference not in approved:
            dropped.append(record)
        elif piece is None or record.reference in whole:
            unapplied.append(record)
        else:
            pin = record.pin()
            tracks[piece][FIELD_OF[record.edge]] = pin.stored()
            tracks[piece][f"{record.edge}_sec"] = pin.proposed()
    return Pinned({**spec, "tracks": tracks}, tuple(unapplied), tuple(dropped))


def pins_of(spec: Mapping[str, Any]) -> list[tuple[int, Edge, CutPin]]:
    """A spec's pins as ``(piece, edge, pin)`` in mix order, start before end."""
    out: list[tuple[int, Edge, CutPin]] = []
    for n, track in enumerate(spec["tracks"]):
        for edge in EDGES:
            raw = track.get(FIELD_OF[edge])
            if raw is not None:
                out.append((n, edge, CutPin.model_validate(raw)))
    return out


def listen_again(
    spec: Mapping[str, Any], analysed: Mapping[str, str | None]
) -> list[tuple[int, Edge]]:
    """The confirmed pins whose file changed (module docstring); ``analysed``: each reference's
    hex SHA-256 of the analysed file the draft holds (``None`` or absent: not known)."""
    references = [str(t["reference"]) for t in spec["tracks"]]
    return [
        (n, edge)
        for n, edge, pin in pins_of(spec)
        if pin.confirmed and (analysed.get(references[n]) or None) != pin.confirmed_against
    ]


def targets(spec: Mapping[str, Any]) -> dict[tuple[str, int, Edge], float]:
    """Each pinned cut's effective second by target (for the moved-by-reanalysis hint)."""
    at = occurrences(spec)
    return {(*at[n], edge): float(spec["tracks"][n][f"{edge}_sec"]) for n, edge, _ in pins_of(spec)}
