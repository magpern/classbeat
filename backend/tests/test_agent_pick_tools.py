"""The pick_pool and choose_songs stages' tools, schemas and rules (ADR 0008 section 3, issue
#179): allowlists, the search registry, the pool checks, the song list checks with the
backend-computed total, kept and replace rules, and where the stored data comes from."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import synthetic_tracks  # noqa: F401  (keeps the shared fixtures importable)
from classbeat.agent.choose_songs import (
    FINAL_TOOL as CHOOSE_FINAL,
)
from classbeat.agent.choose_songs import (
    SYSTEM_PROMPT as CHOOSE_PROMPT,
)
from classbeat.agent.choose_songs import choose_stage
from classbeat.agent.dispatcher import MAX_INPUT_BYTES, MAX_RESULT_BYTES, dispatch
from classbeat.agent.pick_pool import (
    SYSTEM_PROMPT as POOL_PROMPT,
)
from classbeat.agent.pick_pool import (
    PoolResult,
    SearchUnavailable,
    build_query,
    pool_stage,
)
from classbeat.agent.provider import ToolCall
from classbeat.agent.schemas import SearchInput, SongList, SongPool
from classbeat.agent.songs import (
    CROSSFADE_SEC,
    KeptSong,
    PickPlan,
    PoolTrack,
    analysed_pool,
    total_sec,
    within_tolerance,
)
from classbeat.agent.stage import Stage
from classbeat.jobs.errors import JobFailure
from pickfake import POOL_14, case, entries, songs, world

SEED = ["warmup", "build", "climb", "recovery", "sprint", "interval", "build", "cooldown"]


def run(stage: Stage, name: str, args: Any) -> dict[str, Any]:
    return json.loads(dispatch(stage, ToolCall("c1", name, args)).result.content)


def found(w: Any) -> None:
    """Both searches of the standard pick: refs 0 to 19."""
    stage = pool_stage(w.pool)
    run(stage, "search_tracks", {"query": "group0"})
    run(stage, "search_tracks", {"query": "group1"})


# --- the stages -----------------------------------------------------------------------------


def test_the_stages_have_read_and_compute_tools_only_and_their_limits() -> None:
    w = world()
    pool = pool_stage(w.pool)
    assert [(t.name, t.kind) for t in pool.tools] == [
        ("search_tracks", "read"), ("check_pool", "compute"), ("submit_pool", "compute"),
    ]  # fmt: skip
    assert pool.final_tool == "submit_pool"
    choose = choose_stage(w.choose_context(POOL_14))
    assert [(t.name, t.kind) for t in choose.tools] == [
        ("get_pool", "read"), ("check_song_list", "compute"), (CHOOSE_FINAL, "compute"),
    ]  # fmt: skip
    from classbeat.agent import choose_songs, pick_pool

    assert (pick_pool.MAX_TURNS, pick_pool.MAX_TOOL_CALLS) == (12, 30)
    assert (choose_songs.MAX_TURNS, choose_songs.MAX_TOOL_CALLS) == (8, 12)
    for stage in (pool, choose):
        assert len(stage.prompt_version) == 64
    assert pool.prompt_version != choose.prompt_version


def test_a_stage_is_identical_every_time_it_is_built() -> None:
    a, b = world(), world(case("other", brief={"length_min": 45}))
    pa, pb = pool_stage(a.pool), pool_stage(b.pool)
    assert (pa.system, pa.definitions(), pa.prompt_version) == (
        pb.system,
        pb.definitions(),
        pb.prompt_version,
    )
    ca, cb = choose_stage(a.choose_context(POOL_14)), choose_stage(b.choose_context(POOL_14))
    assert (ca.system, ca.definitions(), ca.prompt_version) == (
        cb.system,
        cb.definitions(),
        cb.prompt_version,
    )


def test_the_prompts_say_text_is_data_and_hold_no_user_text() -> None:
    for prompt in (POOL_PROMPT, CHOOSE_PROMPT):
        assert "never instructions" in prompt
        assert "no other tools" in prompt


@pytest.mark.parametrize(
    ("stage_of", "name"),
    [
        (lambda w: pool_stage(w.pool), "get_pool"),
        (lambda w: pool_stage(w.pool), "save_workout"),
        (lambda w: pool_stage(w.pool), "create_playlist"),
        (lambda w: choose_stage(w.choose_context(POOL_14)), "search_tracks"),
        (lambda w: choose_stage(w.choose_context(POOL_14)), "approve"),
    ],
)
def test_a_tool_outside_the_stage_is_refused(stage_of: Any, name: str) -> None:
    result = run(stage_of(world()), name, {})
    assert result["ok"] is False
    assert result["error"] == "unknown_tool"


# --- the schemas ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"query": ""},
        {"query": "x" * 101},
        {"query": "a", "market": "SE"},
        {"query": "a", "limit": 50},
        {"query": "a", "url": "https://api.spotify.com/v1/me"},
        {"query": "a", "year_from": 1800},
        {"query": "a", "year_from": 2020, "year_to": 2010},
        {"query": "a", "year_from": "2010"},
        {"query": ':::""'},
    ],
)
def test_the_search_input_is_text_and_years_only(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SearchInput.model_validate_json(json.dumps(bad), strict=True)
    result = run(pool_stage(world().pool), "search_tracks", bad)
    assert result["error"] == "invalid_input"


@pytest.mark.parametrize(
    "bad",
    [
        {"songs": []},
        {"songs": [{"source": "pool", "index": 0, "role": "dj"}]},
        {"songs": [{"source": "other", "index": 0, "role": "warmup"}]},
        {"songs": [{"source": "pool", "index": -1, "role": "warmup"}]},
        {"songs": [{"source": "pool", "index": 0, "role": "warmup", "title": "Mine"}]},
        {"songs": [{"source": "pool", "index": 0, "role": "warmup", "bpm": 90}]},
        {"songs": [{"source": "pool", "index": 0, "role": "warmup", "spotify_id": "x" * 22}]},
        {"songs": [{"source": "pool", "index": 0, "role": "warmup"}] * 41},
        {"songs": [{"source": "pool", "index": "0", "role": "warmup"}]},
    ],
)
def test_the_song_list_has_no_field_for_a_title_a_bpm_a_length_or_an_id(
    bad: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        SongList.model_validate_json(json.dumps(bad), strict=True)


def test_the_pool_schema_refuses_extra_fields_and_too_many_entries() -> None:
    with pytest.raises(ValidationError):
        SongPool.model_validate_json(
            json.dumps({"entries": [{"ref": 0, "role": "warmup", "id": "x"}]})
        )
    with pytest.raises(ValidationError):
        SongPool.model_validate_json(json.dumps({"entries": [{"ref": 1, "role": "build"}] * 121}))
    with pytest.raises(ValidationError):
        SongPool.model_validate_json(
            json.dumps({"entries": [{"ref": True, "role": "build"}]}), strict=True
        )


# --- search_tracks --------------------------------------------------------------------------


def test_the_backend_builds_the_query_and_adds_years() -> None:
    q = build_query(SearchInput(query="hill climb", year_from=1990, year_to=1999))
    assert q == "hill climb year:1990-1999"
    assert build_query(SearchInput(query="x", year_from=1999, year_to=1999)) == "x year:1999"
    assert build_query(SearchInput(query="x", year_to=1999)) == "x year:1900-1999"
    assert build_query(SearchInput(query="x")) == "x"


def test_the_models_text_cannot_become_a_spotify_filter_or_phrase() -> None:
    w = world()
    run(pool_stage(w.pool), "search_tracks", {"query": 'artist:"Foo" year:1990 tag:new  isrc:X'})
    run(pool_stage(w.pool), "search_tracks", {"query": "line\nbreak‮\u0000 ok"})
    assert w.catalogue.queries == ["artist Foo year 1990 tag new isrc X", "line break ok"]


def test_a_search_gives_refs_not_ids_and_the_same_track_the_same_ref() -> None:
    w = world()
    stage = pool_stage(w.pool)
    first = run(stage, "search_tracks", {"query": "group0"})
    assert first["ok"] is True
    assert [t["ref"] for t in first["tracks"]] == list(range(10))
    assert set(first["tracks"][0]) == {"ref", "title", "artist", "album", "year", "duration_sec"}
    again = run(stage, "search_tracks", {"query": "group0 3"})
    assert [t["ref"] for t in again["tracks"]] == [3]
    assert "SmokePick" not in json.dumps([first, again])
    assert len(w.registry) == 10


def test_a_search_year_filter_reaches_the_searcher_as_year() -> None:
    w = world()
    out = run(
        pool_stage(w.pool), "search_tracks", {"query": "group0", "year_from": 2003, "year_to": 2004}
    )
    assert [t["year"] for t in out["tracks"]] == [2003, 2004]
    assert w.catalogue.queries == ["group0 year:2003-2004"]


def test_a_search_is_at_most_ten_tracks() -> None:
    w = world(searcher=lambda q: [
        {"id": f"Flood{i:017d}", "title": f"T{i}", "artists": ["A"], "sec": 200, "year": "2001"}
        for i in range(25)
    ])  # fmt: skip
    out = run(pool_stage(w.pool), "search_tracks", {"query": "x"})
    assert len(out["tracks"]) == 10


def test_a_failed_search_is_a_fixed_sentence_without_the_providers_text() -> None:
    def broken(query: str) -> Any:
        raise SearchUnavailable("planted provider text 91bd")

    out = run(pool_stage(world(searcher=broken).pool), "search_tracks", {"query": "x"})
    assert out == {
        "ok": False,
        "error": "upstream_unavailable",
        "messages": ["search is not available right now"],
    }


def test_a_full_registry_stops_new_candidates_but_keeps_the_known_ones() -> None:
    w = world(case("big", catalogue={"size": 60}))
    object.__setattr__(w.registry, "_max", 12)
    stage = pool_stage(w.pool)
    assert len(run(stage, "search_tracks", {"query": "group0"})["tracks"]) == 10
    partly = run(stage, "search_tracks", {"query": "group1"})
    assert len(partly["tracks"]) == 2
    full = run(stage, "search_tracks", {"query": "group2"})
    assert (full["error"], len(w.registry)) == ("limit_exceeded", 12)
    assert (
        len(run(stage, "search_tracks", {"query": "group0"})["tracks"]) == 10
    )  # known: no room needed


def test_search_hits_that_are_not_songs_are_skipped_and_text_is_cleaned_and_cut() -> None:
    hits = [
        {"id": "short", "title": "Bad id", "artists": ["A"], "sec": 200},
        {"id": "A" * 22, "title": "Jingle", "artists": ["A"], "sec": 5},
        {"id": "B" * 22, "title": "Podcast", "artists": ["A"], "sec": 5000},
        {"id": "C" * 22, "title": "", "artists": ["A"], "sec": 200},
        {"id": "D" * 22, "title": "No artist", "artists": [], "sec": 200},
        {"id": "E" * 22, "title": "No length", "artists": ["A"], "sec": None},
        {"id": "F" * 22, "title": "Ok‮\u0000 title " + "x" * 200, "artists": ["Ar\tt", "Two"],
         "album": "Al​bum", "sec": 200, "year": "1999"},
    ]  # fmt: skip
    w = world(searcher=lambda q: hits)
    out = run(pool_stage(w.pool), "search_tracks", {"query": "x"})["tracks"]
    assert len(out) == 1
    assert out[0]["title"].startswith("Ok title xxx")
    assert len(out[0]["title"]) == 120
    assert out[0]["artist"] == "Ar t, Two"
    assert out[0]["album"] == "Album"
    assert out[0]["year"] == 1999


def test_an_oversized_input_is_refused_before_anything_runs() -> None:
    w = world()
    huge = {"entries": [{"ref": 0, "role": "x" * 40000}]}
    assert len(json.dumps(huge)) > MAX_INPUT_BYTES
    assert run(pool_stage(w.pool), "check_pool", huge)["error"] == "too_large"
    assert (
        run(
            choose_stage(w.choose_context(POOL_14)),
            "check_song_list",
            {"songs": [], "note": "x" * 40000},
        )["error"]
        == "too_large"
    )


# --- check_pool and submit_pool -------------------------------------------------------------


def pool_call(w: Any, name: str, pairs: Any) -> dict[str, Any]:
    stage = pool_stage(w.pool)
    run(stage, "search_tracks", {"query": "group0"})
    run(stage, "search_tracks", {"query": "group1"})
    return run(stage, name, entries(*pairs))


def test_a_good_pool_checks_and_submits_with_the_ids_from_the_registry() -> None:
    w = world()
    ok = pool_call(w, "check_pool", POOL_14)
    assert ok["ok"]
    assert ok["valid"]
    assert ok["entries"] == 14
    assert ok["by_role"]["sprint"] == 4
    done = dispatch(pool_stage(w.pool), ToolCall("c", "submit_pool", entries(*POOL_14)))
    assert done.ok
    assert done.final
    assert isinstance(done.output, PoolResult)
    assert [p.spotify_id for p in done.output.picks][:2] == [
        "SmokePick0000000000000",
        "SmokePick0000000000001",
    ]
    assert all(w.registry.spotify_id(p.ref) == p.spotify_id for p in done.output.picks)


@pytest.mark.parametrize("ref", [20, 299, 5000])
def test_a_ref_outside_the_registry_is_an_error_result(ref: int) -> None:
    out = pool_call(world(), "check_pool", [*POOL_14, (ref, "build")])
    assert (out["ok"], out["error"]) == (False, "out_of_range")
    assert "not a ref" in out["messages"][0]


def test_a_ref_before_any_search_is_out_of_range_and_the_final_tool_counts_it() -> None:
    w = world()
    done = dispatch(pool_stage(w.pool), ToolCall("c", "submit_pool", entries((0, "warmup"))))
    assert (done.error, done.final, done.output) == ("out_of_range", True, None)


def test_a_duplicate_ref_and_a_duplicate_song_are_errors() -> None:
    w = world(
        case(
            "dup",
            catalogue={
                "size": 40,
                "titles": {"1": "smoke song 0 "},
                "artists": {"1": "Smoke Artist 0"},
            },
        )
    )
    out = pool_call(w, "check_pool", [*POOL_14, (3, "build")])
    assert out["error"] == "invalid_pool"
    assert "ref 3 is in the pool twice" in out["messages"]
    assert "ref 1 is the same song as ref 0" in out["messages"]


@pytest.mark.parametrize("count", [3, 7, 33, 40])
def test_the_pool_size_is_n_to_four_n(count: int) -> None:
    w = world(case("sized", catalogue={"size": 60}))
    stage = pool_stage(w.pool)
    for g in range(4):
        run(stage, "search_tracks", {"query": f"group{g}"})
    base = [(i, "warmup" if i % 2 else "build") for i in range(count)]
    if count > 3:
        base[0:6] = [
            (0, "warmup"),
            (1, "build"),
            (2, "climb"),
            (3, "climb"),
            (4, "cooldown"),
            (5, "cooldown"),
        ]
    out = run(stage, "check_pool", entries(*base[:count]))
    assert out["error"] == "invalid_pool"
    assert any("needs 8 to 32 entries" in m for m in out["messages"])


def test_every_role_the_brief_asks_for_needs_two_candidates_per_slot() -> None:
    thin = [p for p in POOL_14 if p[1] != "sprint"] + [(6, "sprint"), (7, "sprint"), (8, "sprint")]
    out = pool_call(world(), "check_pool", thin)
    assert out["error"] == "invalid_pool"
    assert out["messages"] == ["the pool needs at least 4 sprint candidates; it has 3"]
    races = case("races", brief={"length_min": 30, "races": 2, "ftp_test": 5})
    out = pool_call(world(races), "check_pool", POOL_14)
    needs = " ".join(out["messages"])
    assert "at least 4 race candidates" in needs
    assert "at least 2 interval candidates" in needs
    assert "climb" not in needs
    assert "sprint" not in needs


def test_the_pool_needs_an_opener_and_a_closer() -> None:
    no_open = [p for p in POOL_14 if p[1] not in ("warmup", "build")]
    no_open += [(14, "recovery"), (15, "recovery")]
    out = pool_call(world(), "check_pool", no_open)
    assert any("warmup or build" in m for m in out["messages"])
    no_close = [(r, "recovery") if role == "cooldown" else (r, role) for r, role in POOL_14]
    out = pool_call(world(), "check_pool", no_close)
    assert any("cooldown candidates" in m for m in out["messages"])


# --- replace mode ---------------------------------------------------------------------------


def replace_world() -> Any:
    return world(
        case("rep", catalogue={"size": 30}, replace={"seed_roles": SEED, "replace": [2, 5]})
    )


def test_a_replace_plan_keeps_the_other_slots_and_needs_two_candidates_per_replaced_slot() -> None:
    w = replace_world()
    plan = w.plan
    assert plan.mode == "replace"
    assert plan.replace == ("climb", "interval")
    assert [k.slot for k in plan.kept] == [0, 1, 3, 4, 6, 7]
    assert plan.pool_range == (6, 24)
    assert plan.list_length == 8
    assert plan.required_roles() == {"climb": 2, "interval": 2}
    stage = pool_stage(w.pool)
    run(stage, "search_tracks", {"query": "group0"})
    good = [
        (0, "climb"),
        (1, "climb"),
        (2, "interval"),
        (3, "interval"),
        (4, "build"),
        (5, "build"),
    ]
    assert run(stage, "check_pool", entries(*good))["valid"] is True
    assert run(stage, "check_pool", entries(*good[:5]))["error"] == "invalid_pool"  # 5 < 6
    short = [
        (0, "climb"),
        (1, "build"),
        (2, "interval"),
        (3, "interval"),
        (4, "build"),
        (5, "build"),
    ]
    assert run(stage, "check_pool", entries(*short))["messages"] == [
        "the pool needs at least 2 climb candidates; it has 1"
    ]


def test_a_pool_may_not_repeat_a_kept_song() -> None:
    kept = KeptSong(0, "K" * 22, "Smoke Song 0", "Smoke Artist 0", "warmup", 100.0, 240)
    plan = PickPlan.build(case().brief, replace=["climb"], kept=[kept])
    w = world(case("k"))
    ctx = type(w.pool)(plan, w.registry, w.catalogue.search)
    stage = pool_stage(ctx)
    run(stage, "search_tracks", {"query": "group0"})
    pairs = [(i, "climb") for i in range(6)]
    out = run(stage, "check_pool", entries(*pairs))
    assert out["error"] == "invalid_pool"
    assert "ref 0 is a song that stays in the class; leave it out" in out["messages"]


def test_kept_songs_need_replace_mode() -> None:
    with pytest.raises(ValueError, match="replace"):
        PickPlan.build(case().brief, kept=[KeptSong(0, "K" * 22, "t", "a", "warmup", 100.0, 240)])


# --- get_pool -------------------------------------------------------------------------------


def test_the_plan_is_computed_from_the_brief() -> None:
    for minutes, n in ((10, 3), (30, 8), (45, 11), (60, 15), (180, 40)):
        plan = PickPlan.build(case(brief={"length_min": minutes}).brief)
        assert (plan.target_sec, plan.n) == (minutes * 60, n)
    assert PickPlan.build(case().brief).pool_range == (8, 32)
    assert PickPlan.build(case(brief={"length_min": 180}).brief).pool_range == (40, 120)


def test_get_pool_shows_the_analysis_without_ids_and_counts_the_omitted() -> None:
    w = world(case("nobpm", catalogue={"size": 40, "no_bpm": [3, 4]}))
    stage = pool_stage(w.pool)
    run(stage, "search_tracks", {"query": "group0"})
    run(stage, "search_tracks", {"query": "group1"})
    ctx = w.choose_context(POOL_14)
    assert ctx.omitted_no_bpm == 2
    assert len(ctx.tracks) == 12
    out = run(choose_stage(ctx), "get_pool", {})
    assert out["ok"]
    assert out["omitted_no_bpm"] == 2
    first = out["tracks"][0]
    assert set(first) == {
        "index",
        "title",
        "artist",
        "role",
        "bpm",
        "length_sec",
        "drops",
        "breakdowns",
    }
    assert (first["index"], first["bpm"], first["length_sec"]) == (0, 100.0, 240)
    assert first["drops"] == [60, 180]
    assert first["breakdowns"] == [120]
    assert [t["index"] for t in out["tracks"]] == list(range(12))
    assert "SmokePick" not in json.dumps(out)


def test_a_pool_with_fewer_usable_tracks_than_songs_is_pool_too_small() -> None:
    w = world(case("small", catalogue={"size": 40, "no_bpm": [0, 1, 2, 3, 4, 5, 6]}))
    stage = pool_stage(w.pool)
    run(stage, "search_tracks", {"query": "group0"})
    with pytest.raises(JobFailure) as failure:
        w.choose_context([(i, "build") for i in range(9)])  # 2 usable < 8 songs
    assert failure.value.category == "pool_too_small"


def test_an_unknown_or_unanalysed_pool_track_is_left_out_not_guessed() -> None:
    w = world()
    stage = pool_stage(w.pool)
    run(stage, "search_tracks", {"query": "group0"})
    wanted = [(w.registry.spotify_id(0) or "", "build"), ("Z" * 22, "build")]
    from classbeat.evals import pick

    tracks, omitted = analysed_pool(pick.analysis_map(w.catalogue.tracks[:3]), wanted)
    assert (len(tracks), omitted) == (1, 1)


def test_a_big_pool_is_cut_by_whole_tracks_under_the_result_cap() -> None:
    long = "x" * 200
    w = world(
        case(
            "long",
            brief={"length_min": 180},
            catalogue={
                "size": 120,
                "titles": {str(i): f"{long}{i}" for i in range(120)},
                "artists": {str(i): f"{long}{i}" for i in range(120)},
            },
        )
    )
    stage = pool_stage(w.pool)
    for g in range(12):
        run(stage, "search_tracks", {"query": f"group{g}"})
    ctx = w.choose_context([(i, "build") for i in range(120)])
    done = dispatch(choose_stage(ctx), ToolCall("c", "get_pool", {}))
    assert done.ok
    assert len(done.result.content.encode()) <= MAX_RESULT_BYTES
    out = json.loads(done.result.content)
    assert out["truncated"]["tracks_total"] == 120
    assert out["truncated"]["tracks_shown"] == len(out["tracks"]) < 120


# --- check_song_list and submit_song_list ---------------------------------------------------


def choose_env(
    c: Any = None, pool: Any = POOL_14, searches: tuple[str, ...] = ("group0", "group1")
) -> tuple[Any, Stage]:
    w = world(c)
    s = pool_stage(w.pool)
    for q in searches:
        run(s, "search_tracks", {"query": q})
    return w, choose_stage(w.choose_context(pool))


GOOD = [
    ("pool", 0, "warmup"), ("pool", 2, "build"), ("pool", 4, "climb"),
    ("pool", 10, "recovery"), ("pool", 6, "sprint"), ("pool", 7, "sprint"),
    ("pool", 11, "recovery"), ("pool", 12, "cooldown"),
]  # fmt: skip


def test_the_total_is_the_sum_less_nine_seconds_per_change_of_song() -> None:
    assert CROSSFADE_SEC == 9.0
    assert total_sec([240] * 8) == 8 * 240 - 7 * 9
    assert total_sec([100]) == 100
    assert total_sec([]) == 0
    assert within_tolerance(1980, 1800)
    assert not within_tolerance(1981, 1800)
    assert within_tolerance(1620, 1800)
    assert not within_tolerance(1619, 1800)
    _, stage = choose_env()
    out = run(stage, "check_song_list", songs(*GOOD))
    assert (out["valid"], out["songs"], out["total_sec"]) == (True, 8, 1857)


def test_a_list_is_stored_from_the_analysis_not_from_the_model() -> None:
    _, stage = choose_env()
    done = dispatch(stage, ToolCall("c", CHOOSE_FINAL, songs(*GOOD, note="Steady.‮\u0000 build")))
    assert done.ok
    assert done.final
    out = done.output
    assert [s.title for s in out.songs] == [f"Smoke Song {i}" for i in (0, 2, 4, 10, 6, 7, 11, 12)]
    assert [s.role for s in out.songs] == [g[2] for g in GOOD]
    assert all(s.new for s in out.songs)
    assert (out.total_sec, out.target_sec, out.mode, out.crossfade_sec) == (1857, 1800, "pick", 9.0)
    assert out.songs[1].spotify_id == "SmokePick0000000000002"
    assert out.songs[1].bpm == 106.0
    assert out.songs[1].length_sec == 240
    assert out.note == "Steady. build"


def test_a_list_is_checked_on_the_total_in_both_directions() -> None:
    _, stage = choose_env()
    short = songs(GOOD[0], GOOD[1], GOOD[2], GOOD[3], ("pool", 12, "cooldown"))
    out = run(stage, "check_song_list", short)
    assert out["error"] == "invalid_song_list"
    assert out["messages"] == [
        "the total time is 1164 s (lengths less 9 s per change of song); "
        "it must be between 1620 and 1980 s"
    ]
    long = songs(*GOOD[:-1], ("pool", 8, "sprint"), ("pool", 9, "sprint"), ("pool", 12, "cooldown"))
    out = run(stage, "check_song_list", long)
    assert "the total time is 2319 s" in out["messages"][0]


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([*GOOD[:-1], ("pool", 12, "recovery")], "the last song must have the role cooldown"),
        ([("pool", 0, "sprint"), *GOOD[1:]], "the first song must have the role warmup or build"),
        ([*GOOD[:6], ("pool", 4, "climb"), GOOD[7]], "song 6: pool index 4 is already in the list"),
        ([*GOOD[:6], ("pool", 12, "recovery"), ("pool", 13, "cooldown")], None),
        ([("kept", 0, "warmup"), *GOOD[1:]], "song 0: there are no kept songs; use source pool"),
    ],
)
def test_the_list_rules(items: list[tuple[str, int, str]], message: str | None) -> None:
    _, stage = choose_env()
    out = run(stage, "check_song_list", songs(*items))
    if message is None:
        assert out["ok"] is True
    else:
        assert out["error"] == "invalid_song_list"
        assert message in out["messages"]


def test_the_same_song_under_two_indexes_is_refused() -> None:
    c = case(
        "same",
        catalogue={
            "size": 40,
            "titles": {"2": "SMOKE song 0!"},
            "artists": {"2": "smoke artist 0"},
        },
    )
    _, stage = choose_env(c)
    out = run(stage, "check_song_list", songs(*GOOD))
    assert "song 1: the same song as song 0" in out["messages"]


@pytest.mark.parametrize(
    "items",
    [
        [*GOOD[:-1], ("pool", 14, "cooldown")],
        [*GOOD[:-1], ("pool", 99, "cooldown")],
        [("kept", 0, "warmup")],
    ],
)
def test_an_index_that_names_no_track_is_out_of_range(items: list[tuple[str, int, str]]) -> None:
    _, stage = choose_env()
    out = run(stage, "check_song_list", songs(*items))
    if items[0][0] == "kept":
        assert out["error"] == "invalid_song_list"  # no kept songs exist in pick mode
    else:
        assert out["error"] == "out_of_range"
        assert "pool index" in out["messages"][0]


def replace_env() -> tuple[Any, Stage]:
    c = case("rep", catalogue={"size": 30}, replace={"seed_roles": SEED, "replace": [2, 5]})
    pool = [
        (0, "climb"),
        (1, "climb"),
        (2, "interval"),
        (3, "interval"),
        (4, "build"),
        (5, "build"),
    ]
    return choose_env(c, pool, ("group0",))


REPLACED = [
    ("kept", 0, "warmup"), ("kept", 1, "build"), ("pool", 0, "climb"), ("kept", 2, "recovery"),
    ("kept", 3, "sprint"), ("pool", 2, "interval"), ("kept", 4, "build"), ("kept", 5, "cooldown"),
]  # fmt: skip


def test_a_replacement_keeps_the_kept_songs_in_their_slots_with_their_roles() -> None:
    _, stage = replace_env()
    out = run(stage, "get_pool", {})
    assert [k["slot"] for k in out["kept_songs"]] == [0, 1, 3, 4, 6, 7]
    assert out["kept_songs"][0]["title"] == "Kept Song 0"
    assert "SmokeKept" not in json.dumps(out)
    done = dispatch(stage, ToolCall("c", CHOOSE_FINAL, songs(*REPLACED)))
    assert done.ok
    assert [s.new for s in done.output.songs] == [
        False,
        False,
        True,
        False,
        False,
        True,
        False,
        False,
    ]
    assert done.output.mode == "replace"
    assert done.output.songs[0].spotify_id == "SmokeKept0000000000000"


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            lambda s: s.__setitem__(0, ("kept", 0, "build")),
            "song 0: a kept song keeps its role, warmup",
        ),
        (
            lambda s: s.__setitem__(slice(0, 2), [s[1], s[0]]),
            "song 0: kept song 1 keeps its slot, position 1",
        ),
        (lambda s: s.__setitem__(2, ("pool", 1, "climb")), None),
        (
            lambda s: s.__setitem__(2, ("kept", 5, "cooldown")),
            "song 2: a replaced slot is filled from the pool",
        ),
        (lambda s: s.pop(3), "the list keeps every slot: it needs 8 songs"),
        (
            lambda s: s.__setitem__(1, ("pool", 4, "build")),
            "kept song 1 is missing; every kept song stays",
        ),
    ],
)
def test_the_kept_and_replace_rules(edit: Any, message: str | None) -> None:
    _, stage = replace_env()
    items = list(REPLACED)
    edit(items)
    out = run(stage, "check_song_list", songs(*items))
    if message is None:
        assert out["ok"] is True
    else:
        assert out["error"] == "invalid_song_list"
        assert message in out["messages"]


def test_a_kept_index_outside_the_kept_songs_is_out_of_range() -> None:
    _, stage = replace_env()
    items = [*REPLACED[:-1], ("kept", 6, "cooldown")]
    out = run(stage, "check_song_list", songs(*items))
    assert (out["error"], out["messages"]) == (
        "out_of_range",
        ["song 7: kept index 6 is not between 0 and 5"],
    )


def test_analysed_tracks_carry_no_repr_of_their_id() -> None:
    t = PoolTrack("SecretSpotifyId0000001", "T", "A", "build", 100.0, 240)
    k = KeptSong(0, "SecretSpotifyId0000002", "T", "A", "build", 100.0, 240)
    assert "SecretSpotifyId" not in repr(t) + repr(k)
    assert "SecretSpotifyId" not in repr(world().registry.add_hits(
        [{"id": "SecretSpotifyId0000003", "title": "T", "artists": ["A"], "sec": 200}]
    ))  # fmt: skip
