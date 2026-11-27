"""Guard (ADR 0018 Amendment 6, #1544; owner, 2026-11-26: running is always available): no switch
or flag hides or refuses a running class, and the worker serves a model with no recorded running
coverage with the stale-route warning (ADR 0012 section 5), as ``test_soundless_no_switch`` does
for "No music".

It replaces the guards that pinned the running gate (``test_running_gate``,
``test_connections_running`` and the flag halves of ``test_admin_settings`` and
``test_agent_running``): what they pinned is gone on purpose, and this file pins that it stays
gone. Pure (source and objects only, no database). The database paths are in
``test_running_no_gate``, ``test_running_jobs``, ``test_save_running``, ``test_refit_draft``,
``test_classes_duplicate`` and ``test_interpret_two_sports``.

- The retired names exist nowhere in ``backend/src`` except the allowlisted history lines (an
  allowlist, so a new mention anywhere fails here; migrations, which are history, are outside
  ``src``), nor in the settings, the flag keys, the effective flags, the connections view, the
  sport module, the duplicate gate or Save's approval.
- ``sport.serve_model``: a running job on an uncovered model is served and warned once per process
  and route key; a covered model logs nothing.
"""

import logging
import re
import sys
from pathlib import Path

import pytest

from agentfake import CONFIG
from classbeat.agent.loop import AgentConfig
from classbeat.config import Settings
from classbeat.connections.routes import ConnectionsOut
from classbeat.design import request_policy, sport
from classbeat.flags import core, effective
from classbeat.models import routes
from classbeat.models.jobs import GroupModel
from classbeat.plans.tables import SETTING_KEYS
from classbeat.saving import approval

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"
RETIRED = re.compile(r"running_enabled|RUNNING_ENABLED|runningEnabled")
# The only lines of the source that may still name the retired flag: history, never a reader.
ALLOWED = {
    (
        "admin/settings.py",
        "ADR 0007 amendment of 2026-11-08, #824; ``running_enabled`` by ADR 0018 Amendment 6, "
        "#1544), so",
    ),
    (
        "admin/settings_checks.py",
        "before its audited session. The running classes' coverage check went with the "
        "``running_enabled``",
    ),
    (
        "connections/routes.py",
        "# No ``running_enabled`` any more (ADR 0018 Amendment 6, #1544): running is always "
        "offered.",
    ),
    (
        "design/sport.py",
        "or a flag: the environment's running switch, the stored ``running_enabled`` and the "
        "effective",
    ),
    (
        "flags/core.py",
        '"No music" is always a choice). Migration 0082 removed the ``running_enabled`` key.',
    ),
}
GONE = {
    sport: (
        "SportGate",
        "configure",
        "current",
        "running_switch",
        "running_on",
        "admit_running",
        "admit_class",
        "still_on",
        "require_evaluated",
    ),
    effective: ("running_switch", "running_enabled"),
    core: ("running_switch",),
    approval: ("running_refused",),
    request_policy: ("allowed_sports",),
    routes: ("every_plan_covers",),
}


def mentions() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if RETIRED.search(line):
                found.add((path.relative_to(SRC).as_posix(), line.strip()))
    return found


def test_the_source_names_the_retired_switch_only_in_its_history_lines() -> None:
    assert mentions() == ALLOWED


def test_the_guard_can_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A planted reader of the retired switch is reported (the allowlist is not a blocklist)."""
    planted = tmp_path / "classbeat"
    (planted / "flags").mkdir(parents=True)
    kept = next(line for path, line in ALLOWED if path == "flags/core.py")
    (planted / "flags" / "core.py").write_text(f"  {kept}\n")
    (planted / "web.py").write_text("on = settings.running_enabled\n")
    monkeypatch.setattr(sys.modules[__name__], "SRC", planted)
    assert ("flags/core.py", kept) in mentions()
    assert ("web.py", "on = settings.running_enabled") in mentions()
    assert mentions() != ALLOWED


@pytest.mark.parametrize("module", list(GONE), ids=lambda m: m.__name__)
def test_no_reader_of_the_switch_is_left(module: object) -> None:
    for name in GONE[module]:  # pyright: ignore[reportArgumentType]
        assert not hasattr(module, name), name


def test_the_settings_keys_and_the_view_have_no_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment that still sets the retired variable starts and reads nothing from it; the
    admin cannot store the key (``unknown_key``, ``core.KEYS``; migration 0082's check)."""
    assert "running_enabled" not in Settings.model_fields
    assert "running_enabled" not in ConnectionsOut.model_fields
    assert "running_enabled" not in core.KEYS
    assert "running_enabled" not in SETTING_KEYS
    monkeypatch.setenv("CLASSBEAT_RUNNING_ENABLED", "false")
    made = Settings(environment="test")
    assert not hasattr(made, "running_enabled")
    assert request_policy.ALL_SPORTS == (sport.CYCLING, sport.RUNNING)


def model(*, covered: bool, route_key: str | None = None) -> GroupModel:
    agent = AgentConfig(model=CONFIG.model, route_key=route_key)
    return GroupModel(provider=object(), agent=agent, running_covered=covered)  # pyright: ignore[reportArgumentType]


@pytest.fixture(autouse=True)
def fresh_warnings() -> None:
    sport._warned.clear()  # pyright: ignore[reportPrivateUsage]


def stale(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "model route stale"]


def test_a_running_job_on_an_uncovered_model_is_served_and_warned_once_per_route_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=sport.__name__):
        for key in ("default", "default", "pro", None, None):
            sport.serve_model(model(covered=False, route_key=key))  # never raises
    warned = stale(caplog)
    assert [r.__dict__["route_key"] for r in warned] == ["default", "pro", None]
    assert {(r.__dict__["stage_group"], r.__dict__["reason"]) for r in warned} == {
        ("design", sport.UNCOVERED)
    }
    assert sport.UNCOVERED == "running_not_evaluated"
    assert all(r.levelno == logging.WARNING for r in warned)


def test_a_covered_model_is_served_without_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=sport.__name__):
        sport.serve_model(model(covered=True, route_key="default"))
    assert stale(caplog) == []
