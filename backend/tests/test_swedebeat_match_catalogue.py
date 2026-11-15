"""The match's gateway shape (ADR 0035 sections 5 and 6): what is sent, classbeat's verdict on the
gateway's items, the "all missing" rule and a failed call's job category. Pure: no database, no
network. The job's own runs on the fake gateway are ``test_swedebeat_match.py``'s.

The match job reaches SwedeBeat only through the gateway: no module of the job names the library's
catalogue calls or the direct ``cloud.swedebeat.se`` client any more.
"""

import ast
from pathlib import Path
from typing import Literal

import pytest

from classbeat.jobs.errors import CATEGORIES as JOB_CATEGORIES
from classbeat.swedebeat_gateway.match_policy import Item, Track
from classbeat.swedebeat_match import confirm_policy, gateway_match, verdict_policy
from classbeat.swedebeat_match.confirm_policy import (
    CACHED_CHECK_SECONDS,
    call_failed,
    confirmed,
)
from classbeat.swedebeat_match.confirm_policy import (
    JOB_CATEGORIES as MATCH_CATEGORIES,
)
from classbeat.swedebeat_match.verdict_policy import (
    SAME_SONG,
    Verdict,
    all_missing,
    sent_pair,
    verdict,
)

TRACK = Track("Blue Monday", "New Order", 241, "sb-ref_1", 125.5)
KEPT = ("Blue Monday", "New Order", 241, "sb-ref_1", 125.5)


def test_the_threshold_is_classbeats_own_085() -> None:
    assert SAME_SONG == 0.85


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (Item("found", 0.85, TRACK), Verdict("found", *KEPT)),
        (Item("found", 1.0, TRACK), Verdict("found", *KEPT)),
        # The gateway's own cut is 0.78: classbeat calls 0.78 to 0.85 unsure.
        (Item("found", 0.849, TRACK), Verdict("unsure", *KEPT)),
        (Item("found", 0.78, TRACK), Verdict("unsure", *KEPT)),
        (Item("found", None, TRACK), Verdict("unsure", *KEPT)),
        (Item("unsure", 0.99, TRACK), Verdict("unsure", *KEPT)),
        (Item("found", 0.95, None), Verdict("missing")),  # no usable track
        (Item("unsure", 0.5, None), Verdict("missing")),
        (Item("missing", 0.0, TRACK), Verdict("missing")),
        (None, Verdict("missing")),  # not sent
        # A length of 0 is kept as no length (the revision keeps 1 to 3600 s).
        (Item("found", 0.9, Track("T", "A", 0, "r")), Verdict("found", "T", "A", None, "r")),
        (Item("found", 0.9, Track("T", "A", None, "r")), Verdict("found", "T", "A", None, "r")),
    ],
)
def test_the_verdict(item: Item | None, expected: Verdict) -> None:
    assert verdict(item) == expected


@pytest.mark.parametrize(
    ("title", "artist", "sent"),
    [
        ("Blue Monday", "New Order", ("Blue Monday", "New Order")),
        ("  Padded  ", " Band ", ("  Padded  ", " Band ")),  # as the list holds it
        ("", "New Order", None),
        ("   ", "New Order", None),
        ("Blue Monday", "", None),
        ("Blue Monday", "\t", None),
        ("t" * 250, "a" * 250, ("t" * 200, "a" * 200)),  # never over the gateway's bound
    ],
)
def test_only_a_title_and_an_artist_are_sent_and_an_empty_one_is_not(
    title: str, artist: str, sent: tuple[str, str] | None
) -> None:
    assert sent_pair(title, artist) == sent


def test_two_songs_with_one_cleaned_title_and_artist_are_one_track() -> None:
    key = gateway_match.dedupe_key
    assert key("Blue Monday - Remastered 2011", "New Order") == key("Blue Monday", " new order ")
    assert key("Blue Monday", "New Order") != key("Blue Monday", "Other Band")


def _items(*statuses: Literal["found", "unsure", "missing"]) -> list[Item]:
    return [Item(s, 0.0, None if s == "missing" else TRACK) for s in statuses]


def test_all_missing_is_suspect_only_from_three_tracks() -> None:
    assert all_missing(_items("missing", "missing", "missing"))
    assert all_missing(_items(*(("missing",) * 40)))
    assert not all_missing(_items("missing", "missing"))
    assert not all_missing(_items("missing", "missing", "unsure"))
    assert not all_missing([])


@pytest.mark.parametrize(
    ("check", "expected"),
    [
        (None, ("upstream_unavailable", CACHED_CHECK_SECONDS)),
        (("bearer_refused", None), ("needs_reconnect", None)),
        (("app_misconfigured", None), ("app_misconfigured", None)),
        (("upstream_unavailable", None), ("upstream_unavailable", None)),
        (("rate_limited", 120), ("rate_limited", 120)),
        (("invalid_response", None), ("invalid_response", None)),
        (("upstream_unavailable", 99), ("upstream_unavailable", None)),  # no wait of its own
        (("something new", None), ("invalid_response", None)),
    ],
)
def test_the_confirmation_maps_the_session_check(
    check: tuple[str, int | None] | None, expected: tuple[str, int | None]
) -> None:
    assert confirmed(check) == expected


def test_the_cache_gap_wait_is_past_the_gateways_300_s() -> None:
    assert CACHED_CHECK_SECONDS == 330


def test_a_refused_bearer_is_never_a_category_of_its_own() -> None:
    with pytest.raises(ValueError, match="confirmed first"):
        call_failed("bearer_refused", None)
    assert call_failed("rate_limited", 30) == ("rate_limited", 30)
    assert call_failed("upstream_unavailable", 30) == ("upstream_unavailable", None)
    assert call_failed("whatever", None) == ("invalid_response", None)
    assert "bearer_refused" not in MATCH_CATEGORIES
    assert set(JOB_CATEGORIES) >= MATCH_CATEGORIES


_JOB_MODULES = (gateway_match, confirm_policy, verdict_policy)
_KIND_FILES = (
    "kind.py",
    "replace.py",
    "gateway_match.py",
    "confirm_policy.py",
    "verdict_policy.py",
)
# What the job no longer uses (ADR 0035 sections 6 and 8): the library's catalogue calls and the
# direct SwedeBeat client (deleted with their modules and constants in SG-4).
_GONE = frozenset(
    {
        "match_track",
        "title_score",
        "login",
        "swedebeat_cloud_async_client",
        "SWEDEBEAT_CLOUD_HOST",
        "SWEDEBEAT_CLOUD_ALLOWLIST",
        "search",
        "catalogue",
    }
)


def _names(source: str) -> set[str]:
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.name for a in node.names}
            names.add(node.module or "")
    return names


def test_the_match_job_names_no_library_catalogue_call_or_direct_client() -> None:
    root = Path(gateway_match.__file__).parent
    for name in _KIND_FILES:
        found = _names((root / name).read_text(encoding="utf-8"))
        assert not found & _GONE, (name, found & _GONE)
        assert "classbeat.swedebeat_match.search" not in found
        assert "classbeat.swedebeat_match.catalogue" not in found
    assert all(module.__name__.startswith("classbeat.swedebeat_match") for module in _JOB_MODULES)
