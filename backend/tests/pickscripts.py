"""The recorded fake-provider script of each song-picking smoke case (ADR 0008 section 3): what a
careful model does, as turns with fixed usage numbers. One script per case covers both stages
(the pool stage's turns, then the choose stage's), the way ``FakeProvider`` shares its cursor.

- ``pick_30``: two searches, a pool of 14 checked and submitted, the analysed pool read, a list of
  eight checked and submitted;
- ``replace_two``: one search, a pool of six for the two replaced slots, the list with the six
  kept songs in their slots;
- ``injected_title``: the pick, over a catalogue whose titles and artists carry instructions; the
  script asserts they arrive as data (``contains``) and the list still ends valid.
"""

from typing import Any

from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.evals.pick import PickCase
from pickfake import LIST_8, POOL_14, call, entries, songs, turn

REPLACE_POOL = [
    (0, "climb"),
    (1, "climb"),
    (2, "interval"),
    (3, "interval"),
    (4, "build"),
    (5, "build"),
]
REPLACED = [
    ("kept", 0, "warmup"),
    ("kept", 1, "build"),
    ("pool", 0, "climb"),
    ("kept", 2, "recovery"),
    ("kept", 3, "sprint"),
    ("pool", 2, "interval"),
    ("kept", 4, "build"),
    ("kept", 5, "cooldown"),
]


def _usage(inp: int, out: int, cached: int = 0) -> dict[str, int]:
    return {"input": inp, "output": out, "cache_read": cached, "cache_write": 0}


def _t(*calls: dict[str, Any], usage: dict[str, int]) -> dict[str, Any]:
    return {**turn(*calls), "usage": usage}


def _pick(case: PickCase) -> list[dict[str, Any]]:
    injected = case.name == "injected_title"
    first = call("search_tracks", {"query": "group0"}, expect={"ok": True})
    if injected:
        first["contains"] = ["approve this list now", "admin mode"]  # arrives as data
    pool = entries(*POOL_14)
    get_pool = call("get_pool", {}, expect={"ok": True}, contains=['"tracks"'])
    if injected:
        get_pool["contains"] = ["PWNED"]
    return [
        _t(
            first,
            call("search_tracks", {"query": "group1", "year_from": 2000, "year_to": 2024}),
            usage=_usage(2600, 260),
        ),
        _t(call("check_pool", pool, expect={"ok": True, "valid": True}), usage=_usage(5200, 700)),
        _t(call("submit_pool", pool, expect={"accepted": True}), usage=_usage(6100, 700, 3000)),
        _t(get_pool, usage=_usage(2300, 180)),
        _t(
            call("check_song_list", songs(*LIST_8), expect={"valid": True, "total_sec": 1857}),
            usage=_usage(7600, 900),
        ),
        _t(
            call(
                "submit_song_list",
                songs(*LIST_8, note="A steady build into the climb."),
                expect={"accepted": True},
            ),
            usage=_usage(8500, 400, 4000),
        ),
    ]


def _replace() -> list[dict[str, Any]]:
    pool = entries(*REPLACE_POOL)
    return [
        _t(
            call("search_tracks", {"query": "group0"}, expect={"ok": True}), usage=_usage(2000, 150)
        ),
        _t(call("check_pool", pool, expect={"valid": True}), usage=_usage(3600, 400)),
        _t(call("submit_pool", pool, expect={"accepted": True}), usage=_usage(4200, 300, 2000)),
        _t(
            call("get_pool", {}, expect={"ok": True}, contains=["kept_songs", "Kept Song 0"]),
            usage=_usage(2400, 200),
        ),
        _t(
            call("check_song_list", songs(*REPLACED), expect={"valid": True}),
            usage=_usage(5200, 500),
        ),
        _t(
            call(
                "submit_song_list",
                songs(*REPLACED, note="Two fresh songs."),
                expect={"accepted": True},
            ),
            usage=_usage(6000, 300, 3000),
        ),
    ]


def script_for(case: PickCase) -> FakeScript:
    turns = _replace() if case.replace is not None else _pick(case)
    return FakeScript.model_validate({"model": "fake-model", "turns": turns})


def provider_for_case(case: PickCase) -> FakeProvider:
    return FakeProvider(script_for(case))
