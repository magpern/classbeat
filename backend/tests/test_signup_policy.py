"""``CLASSBEAT_PUBLIC_SIGNUP`` is the one switch that opens sign-up (ADR 0032 section 7, #976)."""

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.config import Settings
from classbeat.flags.effective import environment_signup_mode
from classbeat.flags.signup_policy import environment_mode

SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat"


@pytest.mark.parametrize(
    ("signup_mode", "public", "turnstile", "environment", "expected"),
    [
        # The flag off: never open, whatever signup_mode says.
        ("invite", False, True, "dev", "invite"),
        ("open", False, True, "dev", "invite"),
        ("open", False, True, "prod", "invite"),
        ("closed", False, True, "prod", "closed"),
        # The flag on opens, from the default signup_mode too.
        ("invite", True, False, "dev", "open"),
        ("invite", True, False, "test", "open"),
        ("open", True, False, "dev", "open"),
        ("invite", True, True, "prod", "open"),
        ("open", True, True, "prod", "open"),
        # The emergency brake wins over the flag.
        ("closed", True, True, "dev", "closed"),
        ("closed", True, True, "prod", "closed"),
        # In prod with Turnstile off (the human degrade, ADR 0025 section 5): back to invite.
        ("invite", True, False, "prod", "invite"),
        ("open", True, False, "prod", "invite"),
    ],
)
def test_environment_mode(
    signup_mode: Any, public: bool, turnstile: bool, environment: str, expected: str
) -> None:
    mode = environment_mode(
        signup_mode=signup_mode,
        public_signup=public,
        turnstile_enabled=turnstile,
        environment=environment,
    )
    assert mode == expected


def test_public_signup_is_off_by_default() -> None:
    settings = Settings()
    assert settings.public_signup is False
    assert settings.signup_mode == "invite"
    assert environment_signup_mode(settings) == "invite"


def test_open_alone_is_refused_at_start_up() -> None:
    with pytest.raises(ValidationError, match="signup_mode=open needs CLASSBEAT_PUBLIC_SIGNUP"):
        Settings(signup_mode="open")
    with pytest.raises(ValidationError, match="signup_mode=open needs CLASSBEAT_PUBLIC_SIGNUP"):
        Settings(signup_mode="open", public_signup=False)


def test_the_flag_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_PUBLIC_SIGNUP", "true")
    settings = Settings()
    assert settings.public_signup is True
    assert environment_signup_mode(settings) == "open"
    monkeypatch.setenv("CLASSBEAT_SIGNUP_MODE", "closed")
    assert environment_signup_mode(Settings()) == "closed"


def test_the_settings_reader_passes_every_field() -> None:
    def mode(**fields: Any) -> str:
        base = {
            "signup_mode": "invite",
            "public_signup": True,
            "turnstile_enabled": True,
            "environment": "prod",
        }
        return environment_signup_mode(SimpleNamespace(**(base | fields)))  # type: ignore[arg-type]

    assert mode() == "open"
    assert mode(public_signup=False) == "invite"
    assert mode(turnstile_enabled=False) == "invite"
    assert mode(environment="dev", turnstile_enabled=False) == "open"
    assert mode(signup_mode="closed") == "closed"


# Only ``flags.effective.environment_signup_mode`` reads the two fields; every route asks it (or
# ``flags.effective.signup_mode``), so no route can open sign-up past the flag.
READERS = {"public_signup": {"flags/effective.py"}, "signup_mode": {"flags/effective.py"}}


@pytest.mark.parametrize("field", sorted(READERS))
def test_only_the_policy_reads_the_sign_up_fields(field: str) -> None:
    pattern = re.compile(rf"settings\.{field}\b")
    found = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    }
    assert found == READERS[field]
