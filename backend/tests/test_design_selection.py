"""ADR 0017 P3: ``Brief.class_type``, its derivation and ``select_profile_for_brief``."""

import json
import uuid
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from classbeat.agent import model_input
from classbeat.agent.schemas import Brief
from classbeat.design import kind as design_kind
from classbeat.design import revision
from classbeat.design.rules import enums as E
from classbeat.design.rules import select_profile
from classbeat.design.selection import resolve_class_type, select_profile_for_brief
from classbeat.jobs.inputs import jsonb_size


def _b(**kw: Any) -> Brief:
    return Brief.model_validate({"length_min": 45, **kw})


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, "plain"),
        ({"intensity": "easy"}, "plain"),
        ({"climbs": 3}, "plain"),
        ({"style": "hiit tabata", "text": "journey to the sea"}, "plain"),  # never invented
        ({"intensity": "hard"}, "interval"),
        ({"sprints": 2}, "interval"),
        ({"races": 1}, "interval"),
        ({"ftp_test": 20}, "ftp_test"),
        ({"ftp_test": 20, "sprints": 4, "intensity": "hard"}, "ftp_test"),
    ],
)
def test_derivation_table(kw: dict[str, Any], expected: str) -> None:
    choice = resolve_class_type(_b(**kw))
    assert (choice.class_type, choice.derived, choice.source) == (expected, True, "derived")


def test_explicit_type_wins_and_is_not_derived() -> None:
    choice = resolve_class_type(_b(class_type="plain", sprints=5, intensity="hard"))
    assert (choice.class_type, choice.derived, choice.source) == ("plain", False, "brief")
    assert resolve_class_type(_b(class_type="hiit")).class_type == "hiit"


def test_enum_matches_rules_and_refuses_others() -> None:
    literal = get_args(Brief.model_fields["class_type"].annotation)[0]
    assert set(get_args(literal)) == set(E.CLASS_TYPES)
    for bad in ("spin", "", "Plain", 1, ["plain"]):
        with pytest.raises(ValidationError):
            _b(class_type=bad)


def test_old_brief_validates_and_dumps_unchanged() -> None:
    old = {
        "length_min": 45, "intensity": "moderate", "climbs": 0, "sprints": 0, "races": 0,
        "ftp_test": None, "style": "", "text": "",
    }  # fmt: skip
    brief = Brief.model_validate(old)
    assert brief.class_type is None
    assert brief.model_dump(mode="json") == old
    assert "class_type" not in json.dumps(brief.model_dump(mode="json"))
    assert "class_type" not in Brief.model_validate({"length_min": 45}).model_dump()
    dumped = _b(class_type="interval").model_dump(mode="json")
    assert dumped["class_type"] == "interval"
    assert Brief.model_validate(dumped).class_type == "interval"


def test_model_input_ignores_class_type() -> None:
    assert model_input.brief_for_model(_b(class_type="interval")) == model_input.brief_for_model(
        _b()
    )
    assert "class_type" not in model_input.brief_for_model(_b(class_type="interval"))


def _job(brief: dict[str, Any]) -> design_kind.DesignDraftInput:
    return design_kind.DesignDraftInput.model_validate(
        {
            "title": "Tuesday climb",
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": "a" * 22},
            "mowl_connection_id": str(uuid.uuid4()),
            "brief": brief,
        }
    )


def test_job_input_size_guard_unchanged() -> None:
    assert revision.MAX_BRIEF_BYTES == 4096
    assert jsonb_size(_b().model_dump(mode="json")) == jsonb_size(
        {
            "length_min": 45, "intensity": "moderate", "climbs": 0, "sprints": 0, "races": 0,
            "ftp_test": None, "style": "", "text": "",
        }
    )  # fmt: skip
    _job({"length_min": 45, "text": "x" * 2000})
    big = {"length_min": 45, "text": "åäö\U0001f6b4" * 500}
    with pytest.raises(ValueError, match="too long"):
        _job(big)
    with pytest.raises(ValueError, match="too long"):
        _job({**big, "class_type": "plain"})


def test_job_input_accepts_and_refuses_class_type() -> None:
    assert _job({"length_min": 45, "class_type": "interval"}).brief.class_type == "interval"
    assert _job({"length_min": 45}).brief.class_type is None
    with pytest.raises(ValidationError):
        _job({"length_min": 45, "class_type": "spin"})


@pytest.mark.parametrize(
    ("kw", "pid"),
    [
        ({"length_min": 45}, "cycling.plain.45"),
        ({"length_min": 30, "sprints": 3}, "cycling.interval.30"),
        ({"length_min": 60, "intensity": "hard"}, "cycling.interval.60"),
        ({"length_min": 46, "class_type": "plain", "sprints": 3}, "cycling.plain.45"),
    ],
)
def test_select_profile_for_brief(kw: dict[str, Any], pid: str) -> None:
    p = select_profile_for_brief(Brief.model_validate(kw))
    assert p is not None
    assert f"{p.sport}.{p.class_type}.{p.label_minutes}" == pid


@pytest.mark.parametrize(
    "kw",
    [
        {"length_min": 75},
        {"length_min": 45, "class_type": "hiit"},
        {"length_min": 45, "ftp_test": 20},
        {"length_min": 45, "class_type": "journey"},
        {"length_min": 20},
    ],
)
def test_no_profile_is_none_not_an_error(kw: dict[str, Any]) -> None:
    assert select_profile_for_brief(Brief.model_validate(kw)) is None


def test_matches_explicit_select_profile() -> None:
    assert select_profile_for_brief(_b(sprints=1)) == select_profile("interval", 45)
