"""SR C7's pure rules (ADR 0036 sections 6 and 8, ADR 0005 section 7): the output keys, the state
mapping and its safe actions, the MOWL match (**never by name**), the written bodies, and the
gateway write outcomes and answers. No I/O.
"""

import json
from uuid import UUID

import pytest

from classbeat.studio import mowl_match_policy as match
from classbeat.studio import output_body_policy as body
from classbeat.studio import output_key_policy as keys
from classbeat.studio.output_state_policy import actions, derived, note
from classbeat.swedebeat_gateway import write_policy as wp

CLASS = UUID("11111111-1111-4111-8111-111111111111")
REVISION = UUID("22222222-2222-4222-8222-222222222222")
URL = "https://mixes.example/rider%40x/My_mix-1.mp3"

# --- Keys ---------------------------------------------------------------------------------------


def test_a_key_is_bound_to_the_approval_and_the_attempt() -> None:
    made = keys.key(CLASS, REVISION, "swedebeat_playlist", 1, "create")
    assert made == f"out:{CLASS}:{REVISION}:swedebeat_playlist:1:create"
    assert keys.parse(made) == keys.OutputKey(CLASS, REVISION, "swedebeat_playlist", 1, "create")
    assert str(keys.parse(made)) == made
    assert keys.key(CLASS, REVISION, "swedebeat_playlist", 2, "create") != made  # a new attempt


@pytest.mark.parametrize(
    ("output", "attempt", "step"),
    [("swedebeat_mix", 1, "create"), ("mowl", 0, "attach"), ("mowl", 1001, "attach")],
)
def test_a_step_of_another_output_or_a_bad_attempt_is_no_key(
    output: str, attempt: int, step: str
) -> None:
    with pytest.raises(ValueError, match="not a step"):
        keys.key(CLASS, REVISION, output, attempt, step)


@pytest.mark.parametrize(
    "value",
    [
        f"out:{CLASS}:{REVISION}:swedebeat_mix:1:create",  # a step of another output
        f"out:{CLASS}:{REVISION}:swedebeat_mix:01:merge",
        f"out:{CLASS}:{REVISION}:swedebeat_mix:1:merge:x",
        f"out:AAAAAAAA-1111-4111-8111-111111111111:{REVISION}:swedebeat_mix:1:merge",
        f"{CLASS}:pool-create",
        "",
    ],
)
def test_only_an_output_key_parses(value: str) -> None:
    assert keys.parse(value) is None


# --- States and actions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operations", "stored", "job_open", "state"),
    [
        ({}, "pending", False, "pending"),
        ({"create": ("pending", True)}, "running", True, "running"),
        ({"create": ("pending", False)}, "running", False, "in_doubt"),  # an orphan
        ({"create": ("uncertain", False)}, "running", False, "in_doubt"),
        ({"create": ("succeeded", False), "publish": ("uncertain", False)}, "running", False,
         "in_doubt"),
        ({"create": ("succeeded", False), "publish": ("failed", False)}, "failed", False,
         "failed"),
        ({"create": ("succeeded", False), "publish": ("succeeded", False)}, "running", False,
         "done"),
        ({"create": ("succeeded", False)}, "running", False, "failed"),  # ended between steps
        ({"create": ("succeeded", False)}, "running", True, "running"),
        ({"create": ("succeeded", False), "publish": ("succeeded", False)}, "in_doubt", False,
         "in_doubt"),  # sticky until the user acts
        ({}, "published_not_listed", False, "published_not_listed"),
    ],
)  # fmt: skip
def test_the_state_is_derived_from_the_operations(
    operations: dict[str, tuple[str, bool]], stored: str, job_open: bool, state: str
) -> None:
    assert derived("swedebeat_playlist", operations, stored, job_open=job_open) == state


def test_the_safe_actions_and_notes() -> None:
    assert actions("in_doubt") == ["check_again", "create_another"]
    assert actions("in_doubt", candidate=True) == ["check_again", "confirm", "create_another"]
    assert actions("published_not_listed") == ["check_again"]
    assert actions("failed") == ["retry"]
    assert actions("done") == actions("running") == []
    assert note("swedebeat_playlist", "failed", ["create"], {}) == "created_not_public"
    assert note("swedebeat_playlist", "in_doubt", [], {}) == "may_exist"
    assert note("swedebeat_mix", "in_doubt", [], {"candidate": {"name": "x"}}) == (
        "listed_candidate"
    )
    assert note("swedebeat_mix", "failed", [], {}) == "not_sent"
    assert note("swedebeat_playlist", "done", ["create", "publish"], {}) is None


# --- The MOWL match ------------------------------------------------------------------------------


def listed(url: object, ms: object = 600_000, name: str = "Ride · cb-abc123", mix_id: str = "m1"):
    return {"ID": mix_id, "Name": name, "URL": url, "DurationInMilliseconds": ms}


def test_the_mix_is_attached_by_its_object_key_and_duration_only() -> None:
    other_host = "https://cdn.example/rider@x/My_mix-1.mp3"  # the same object key elsewhere
    found = match.attachable(match.entries([listed(other_host)]), URL, 600.0)
    assert found is not None
    assert found.mix_id == "m1"
    assert match.attachable(match.entries([listed(URL, 603_000)]), URL, 600.0) is None
    assert match.attachable(match.entries([listed(URL, 601_500)]), URL, 600.0) is not None
    two = [listed(URL, mix_id="a"), listed(other_host, mix_id="b")]
    assert match.attachable(match.entries(two), URL, 600.0) is None  # never one of two


def test_a_name_only_match_never_attaches() -> None:
    by_name = match.entries([listed("https://mixes.example/another.mp3")])
    assert match.attachable(by_name, URL, 600.0) is None
    assert match.attachable(match.entries([listed(None)]), URL, 600.0) is None
    assert [e.mix_id for e in match.named(by_name, "Ride · cb-abc123")] == ["m1"]  # shown only
    assert match.named(by_name, "Ride · cb-abc12") == []


@pytest.mark.parametrize(
    "entry",
    [listed(URL, mix_id="../x"), listed(URL, ms=0), listed(URL, ms=True), listed(URL, ms="600"),
     "not an entry", listed(""), listed("http://mixes.example/a.mp3"),
     listed("https://mixes.example/a.mp3?sig=1"), listed("https://u@mixes.example/a.mp3"),
     listed("https://mixes.example/"), listed("https://m.example/" + "a" * 2048), listed(None)],
)  # fmt: skip
def test_a_malformed_entry_is_left_out(entry: object) -> None:
    assert match.entries([entry]) == []
    assert match.object_key("ftp://x/y") is None
    assert match.object_key("http://x/y") is None  # only what the merge returns: https
    assert match.object_key("https://x/") is None


# --- The bodies ---------------------------------------------------------------------------------

FACTS = {
    "ref-a": body.Facts("Alpha", "Ann", 128.0),
    "ref-b": body.Facts("Beta", "Bo", 130.0),
}
SPEC = {
    "tracks": [
        {"reference": "ref-a", "start_sec": 0, "end_sec": 200.0},
        {"reference": "ref-b", "start_sec": 10.004, "end_sec": 180.0},
    ],
    "crossfades": [8],
}
DURATIONS = {"ref-a": 200.0, "ref-b": 210.0}


def test_the_playlist_body_is_built_from_the_spec_and_the_facts() -> None:
    made = body.playlist_body("Ride", SPEC, FACTS, DURATIONS)
    assert (made["category_id"], made["crossfade_sec"], made["name"]) == (27, 0, "Ride")
    first, last = made["tracks"]
    assert (first["title"], first["trim"], first["crossfade_sec"]) == ("Alpha", None, 8)
    assert (last["start_sec"], last["trim"], last["crossfade_sec"]) == (10.0, True, 0)


def test_the_names_are_printable_cut_and_the_mix_name_unique() -> None:
    assert body.class_name(None) == body.class_name(" \n ") == body.FALLBACK_NAME
    assert body.class_name("a\x00b") == "a b"
    assert len(body.class_name("x" * 500)) == 120
    suffix = body.new_suffix()
    assert suffix.startswith("cb-")
    assert len(suffix) == 9
    assert body.mix_name("x" * 500, suffix) == "x" * 100 + " · " + suffix
    assert body.new_suffix() != suffix
    with pytest.raises(ValueError, match="suffix"):
        body.mix_name("Ride", "nope")


def test_x_tracks_add_up_to_the_mix_and_fit_16_kb() -> None:
    plan = body.render_plan(SPEC, DURATIONS)
    played = body.played_seconds(plan)
    assert [p["reference"] for p in plan] == ["ref-a", "ref-b"]
    assert played[0] == pytest.approx(200.0 - 8.0, abs=0.01)  # less its outgoing crossfade
    long = {r: body.Facts("é" * 300, "ü" * 300, 1.0) for r in FACTS}
    items = body.x_tracks(plan * 20, long)
    assert len(wp.x_tracks_header(items)) <= wp.X_TRACKS_MAX


# --- The gateway's write outcomes and answers ---------------------------------------------------


@pytest.mark.parametrize(
    ("status", "unknown", "category"),
    [
        (201, False, None),
        (401, True, "app_misconfigured"),
        (401, False, "bearer_refused"),
        (403, False, "writes_off"),  # the write guard: a state the user sees
        (404, False, "track_unavailable"),
        (413, False, "track_too_large"),
        (422, False, "invalid_input"),
        (429, False, "rate_limited"),
        (200, False, "uncertain"),  # another success than the call's own
        (302, False, "uncertain"),
        (500, False, "uncertain"),
        (502, False, "uncertain"),
        (504, False, "uncertain"),
    ],
)
def test_a_write_is_refused_only_when_the_gateway_says_so(
    status: int, unknown: bool, category: str | None
) -> None:
    assert wp.write_category(status, 201, unknown_client=unknown) == category


def test_the_answers_are_checked() -> None:
    assert wp.parse_created(b'{"playlist_id": "pl-1"}') == "pl-1"
    for bad in (None, b"[]", b'{"playlist_id": "../x"}', b"{", b'{"playlist_id": 1}'):
        assert wp.parse_created(bad) is None
    assert wp.parse_published(b'{"playlist_id": "pl-1", "public": true}', "pl-1")
    assert not wp.parse_published(b'{"playlist_id": "pl-2", "public": true}', "pl-1")
    assert not wp.parse_published(b'{"playlist_id": "pl-1", "public": 1}', "pl-1")
    assert wp.parse_shared(b'[{"playlist_id": "a"}, {"playlist_id": "../"}]') == ["a"]
    assert wp.parse_shared(b"[]") == []
    assert wp.parse_shared(b'{"items": []}') is None
    assert wp.parse_shared(b'["a"]') is None
    assert wp.parse_merged(json.dumps({"url": URL}).encode()) == URL
    for url in (
        "http://mixes.example/a.mp3",
        "https://u@mixes.example/a.mp3",
        "https://mixes.example/a.mp3?x=1",
        "https://mixes.example/",
        "https://x/a#f",
        " https://mixes.example/a.mp3",  # urlsplit would drop the blanks
        "https://mixes.example/a.mp3 ",
        "https://mixes.example/a b.mp3",
    ):
        assert wp.parse_merged(json.dumps({"url": url}).encode()) is None  # fmt: skip


def test_the_publish_path_takes_a_checked_id_only() -> None:
    assert wp.publish_path("pl-1") == "/playlists/pl-1/publish"
    for bad in ("", "../shared", "a/b", "a?b", "x" * 65):
        with pytest.raises(ValueError, match="playlist id"):
            wp.publish_path(bad)
    assert json.loads(wp.PUBLISH_BODY) == {"confirm_irreversible": True}


def test_x_tracks_is_one_to_200_items_of_at_most_16_kb() -> None:
    assert json.loads(wp.x_tracks_header([{"title": "Å"}])) == [{"title": "Å"}]
    for bad in ([], [{}] * 201, [{"title": "x" * 17_000}]):
        with pytest.raises(ValueError, match="X-Tracks"):
            wp.x_tracks_header(bad)
