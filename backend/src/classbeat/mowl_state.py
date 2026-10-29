"""What classbeat compares between an intended workout and a MOWL program (ADR 0007 sections 6
to 8): one normal form for both sides.

``normalized_spec(spec, playlist_id)`` is what ``mowl.workout.save`` writes for a spec, and
``normalized_program(program)`` the same fields read back from ``api.get_workout``: the name, the
description, ``public``, the playlist id, and per segment its kind, name and total length, per
interval ``sec``, ``zone``, position (standing), cycle (ACC), the rpm range, the game and the FTP
test. Ids, TSS and timestamps are not in it. ``matches`` compares an intended spec with a read
program through it, and ``state_hash`` is the SHA-256 of its canonical JSON
(``saved_workouts.state_hash``). The save's reconciler and verify step use it; SV2's update
compares the same hash before it writes.

A program field that is missing or of an unexpected type makes ``normalized_program`` raise
``ValueError``: such a program never matches, so a reconciler counts it as a new program that
does not match (inconclusive), never as proof of anything. **Not yet checked against real MOWL**
(ADR 0007, "Live activation"): that ``get_workout`` returns these fields as the library writes
them. MOWL's names for a position and a cycle are read only when the numeric id is absent.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, cast

from mowl import workout

# The library's ids (``workout.POSITION``, ``workout.CYCLE``, ``workout.GAMES``,
# ``workout.FTP_TESTS``); a test checks they are still these.
STANDING_ID: Final = workout.POSITION[True]
ACC_ID: Final = workout.CYCLE[True]
_STANDING_NAMES: Final = frozenset({"standing"})
_ACC_NAMES: Final = frozenset({"acc"})


def _int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("not a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ValueError("not a whole number")


def _text(value: object, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise ValueError("not text")
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("not an object")
    return cast(Mapping[str, Any], value)


def _list(value: object) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValueError("not a list")
    return cast(list[Any], value)


def _interval_from_spec(i: Mapping[str, Any]) -> dict[str, Any]:
    rpm = i.get("rpm", [0, 0])
    game = i.get("game")
    ftp = i.get("ftp_test")
    return {
        "sec": _int(i["sec"]),
        "zone": _int(i["zone"]),
        "standing": bool(i.get("standing")),
        "acc": bool(i.get("acc")),
        "rpm": [_int(rpm[0]), _int(rpm[1])],
        "game": None if game is None else workout.GAMES[game],
        "ftp_test": None if ftp is None else workout.FTP_TESTS[ftp],
    }


def normalized_spec(spec: Mapping[str, Any], playlist_id: int | None) -> dict[str, Any]:
    """What ``workout.save(spec, creds, playlist_id)`` writes, in the normal form."""
    segments: list[dict[str, Any]] = []
    for s in _list(spec["segments"]):
        intervals = [_interval_from_spec(_mapping(i)) for i in _list(s["intervals"])]
        segments.append(
            {
                "kind": _text(s["kind"]),
                "name": _text(s["name"]),
                "sec": sum(i["sec"] for i in intervals),
                "intervals": intervals,
            }
        )
    return {
        "name": _text(spec["name"]),
        "description": _text(spec.get("description", ""), optional=True),
        "public": False,  # the library always writes IsPublic false
        "playlist_id": playlist_id,
        "segments": segments,
    }


def _flag(
    item: Mapping[str, Any], id_key: str, name_key: str, the_id: int, names: frozenset[str]
) -> bool:
    if item.get(id_key) is not None:
        return _int(item[id_key]) == the_id
    return _text(item[name_key]).strip().lower() in names


def _nested_id(item: Mapping[str, Any], key: str, id_key: str) -> int | None:
    value = item.get(key)
    if value is None:
        return None
    return _int(_mapping(value)[id_key])


def _interval_from_program(i: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sec": _int(i["Duration"]),
        "zone": _int(i["ScaleCoggan"]),
        "standing": _flag(i, "PositionTypeID", "PositionTypeName", STANDING_ID, _STANDING_NAMES),
        "acc": _flag(i, "CycleID", "CycleName", ACC_ID, _ACC_NAMES),
        "rpm": [_int(i["RPMFrom"]), _int(i["RPMTo"])],
        "game": _nested_id(i, "IntervalGame", "IntervalGameID"),
        "ftp_test": _nested_id(i, "FtpTest", "FtpTestID"),
    }


def _kind(s: Mapping[str, Any]) -> str:
    flags = [bool(s.get(k)) for k in ("IsWarmup", "IsActiveRecovery", "IsCooldown")]
    if sum(flags) > 1:
        raise ValueError("a segment of two kinds")
    if flags[0]:
        return "warmup"
    if flags[1]:
        return "recovery"
    return "cooldown" if flags[2] else "main"


def normalized_program(program: object) -> dict[str, Any]:
    """A program as ``api.get_workout`` returns it, in the normal form; ``ValueError`` when it
    does not have the fields (the caller treats it as not matching)."""
    try:
        p = _mapping(program)
        segments: list[dict[str, Any]] = []
        for raw in _list(p.get("Segments") or []):
            s = _mapping(raw)
            segments.append(
                {
                    "kind": _kind(s),
                    "name": _text(s["Name"]),
                    "sec": _int(s["TotalDuration"]),
                    "intervals": [
                        _interval_from_program(_mapping(i)) for i in _list(s["Intervals"])
                    ],
                }
            )
        public = p["IsPublic"]
        if not isinstance(public, bool):
            raise ValueError("IsPublic is not a boolean")
        playlist = p.get("PlaylistID")
        return {
            "name": _text(p["Name"]),
            "description": _text(p.get("Description"), optional=True),
            "public": public,
            "playlist_id": None if playlist is None else _int(playlist),
            "segments": segments,
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("not a readable program") from exc


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def state_hash(normalized: Mapping[str, Any]) -> bytes:
    """``saved_workouts.state_hash``: SHA-256 of the normal form's canonical JSON."""
    return hashlib.sha256(canonical(dict(normalized))).digest()


def matches(spec: Mapping[str, Any], program: object, *, playlist_id: int | None) -> bool:
    """The program is private and holds exactly the spec's content with this playlist."""
    try:
        read = normalized_program(program)
    except ValueError:
        return False
    return read["public"] is False and read == normalized_spec(spec, playlist_id)


def program_id_of(item: object) -> int | None:
    """A listing entry's ``ProgramID`` (a positive whole number), else None."""
    if not isinstance(item, Mapping):
        return None
    value = cast(Mapping[str, Any], item).get("ProgramID")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2**63 - 1:
        return None
    return value
