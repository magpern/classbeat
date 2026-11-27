"""Guard (ADR 0007 amendment of 2026-11-27, #1543; owner, 2026-11-26: "No music" is always a
choice): no switch or flag hides or refuses a soundless class, and the worker serves a model with
no recorded soundless coverage with the stale-route warning (ADR 0012 section 5).

Pure (source and objects only, no database). The database paths are in ``test_soundless_jobs``,
``test_soundless_revise``, ``test_save_soundless`` and ``test_interpret_reread``.

- The retired names exist nowhere in ``backend/src`` except the allowlisted history lines (an
  allowlist, so a new mention anywhere fails here), nor in the settings, the effective flags, the
  connections view, the soundless module or Save's approval.
- ``serve_model``: a sketch on an uncovered model is ``model_config``; a real no-music class is
  served, warned once per process and route key; a covered model logs nothing.
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
from classbeat.design import music_kind
from classbeat.flags import effective
from classbeat.jobs import JobFailure
from classbeat.models.jobs import GroupModel
from classbeat.saving import approval

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"
RETIRED = re.compile(r"soundless_enabled|SOUNDLESS_ENABLED|soundlessEnabled")
# The only lines of the source that may still name the retired flag: history, never a reader.
HISTORY = (
    "no key (``soundless_enabled`` was retired, ADR 0007 amendment of 2026-11-08, #824), so it is"
)
ALLOWED = {("admin/settings.py", HISTORY)}
GONE = {
    music_kind: ("switch_on", "still_on", "admit_soundless", "soundless_on"),
    effective: ("soundless_enabled",),
    approval: ("soundless_refused",),
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
    (planted / "admin").mkdir(parents=True)
    (planted / "admin" / "settings.py").write_text(f"  {HISTORY}\n")
    (planted / "web.py").write_text("on = settings.soundless_enabled\n")
    monkeypatch.setattr(sys.modules[__name__], "SRC", planted)
    assert ("admin/settings.py", HISTORY) in mentions()
    assert ("web.py", "on = settings.soundless_enabled") in mentions()
    assert mentions() != ALLOWED


@pytest.mark.parametrize("module", list(GONE), ids=lambda m: m.__name__)
def test_no_reader_of_the_switch_is_left(module: object) -> None:
    for name in GONE[module]:  # pyright: ignore[reportArgumentType]
        assert not hasattr(module, name), name


def test_the_settings_and_the_view_have_no_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment that still sets the retired variable starts and reads nothing from it."""
    assert "soundless_enabled" not in Settings.model_fields
    assert "soundless_enabled" not in ConnectionsOut.model_fields
    monkeypatch.setenv("CLASSBEAT_SOUNDLESS_ENABLED", "false")
    made = Settings(environment="test")
    assert not hasattr(made, "soundless_enabled")
    assert music_kind.SoundlessGate.from_settings(made) == music_kind.SoundlessGate()


def model(*, covered: bool, route_key: str | None = None) -> GroupModel:
    agent = AgentConfig(model=CONFIG.model, route_key=route_key)
    return GroupModel(provider=object(), agent=agent, soundless_covered=covered)  # pyright: ignore[reportArgumentType]


@pytest.fixture(autouse=True)
def fresh_warnings() -> None:
    music_kind._warned.clear()  # pyright: ignore[reportPrivateUsage]


def stale(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "model route stale"]


def test_a_sketch_on_an_uncovered_model_is_model_config() -> None:
    with pytest.raises(JobFailure) as failed:
        music_kind.serve_model(model(covered=False), sketch=True)
    assert failed.value.args == ("model_config",)
    music_kind.serve_model(model(covered=True), sketch=True)  # covered: no refusal


def test_no_music_on_an_uncovered_model_is_served_and_warned_once_per_route_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=music_kind.__name__):
        for key in ("default", "default", "pro", None, None):
            music_kind.serve_model(model(covered=False, route_key=key), sketch=False)
    warned = stale(caplog)
    assert [r.__dict__["route_key"] for r in warned] == ["default", "pro", None]
    assert {(r.__dict__["stage_group"], r.__dict__["reason"]) for r in warned} == {
        ("design", music_kind.UNCOVERED)
    }
    assert all(r.levelno == logging.WARNING for r in warned)


def test_a_covered_model_is_served_without_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=music_kind.__name__):
        music_kind.serve_model(model(covered=True, route_key="default"), sketch=False)
    assert stale(caplog) == []
