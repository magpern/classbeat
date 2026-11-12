"""``CLASSBEAT_PUBLIC_SIGNUP`` is the one switch that opens sign-up (ADR 0032 section 9, #976)."""

import ast
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.config import Settings
from classbeat.flags.effective import environment_signup_held, environment_signup_mode
from classbeat.flags.signup_policy import environment_mode, held_reason

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


# Only ``flags.effective._signup_fields`` reads the two fields; every route asks
# ``environment_signup_mode`` or ``signup_mode`` there, so no route can open sign-up past the flag.
# An AST scan, so a differently named variable (``cfg.signup_mode``), ``getattr`` or
# ``attrgetter`` is caught too. ``<flags.effective>.signup_mode(...)`` is that module's function.
FIELDS = frozenset({"signup_mode", "public_signup"})
READER = ("flags/effective.py", "_signup_fields")


def _effective_aliases(tree: ast.Module) -> set[str]:
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "classbeat.flags"
        for alias in node.names
        if alias.name == "effective"
    }


def _reads(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _effective_aliases(tree)
    allowed: set[int] = set()
    if path.relative_to(SRC).as_posix() == READER[0]:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == READER[1]:
                allowed |= {id(inner) for inner in ast.walk(node)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FIELDS and id(node) not in allowed:
            calls_effective = isinstance(node.value, ast.Name) and node.value.id in aliases
            if not (calls_effective and node.attr == "signup_mode"):
                found.append(f"{node.lineno}: {ast.unparse(node)}")
        if isinstance(node, ast.Call) and any(
            isinstance(arg, ast.Constant) and arg.value in FIELDS for arg in node.args
        ):
            name = ast.unparse(node.func)
            if name.split(".")[-1] in {"getattr", "attrgetter", "__getattribute__"}:
                found.append(f"{node.lineno}: {ast.unparse(node)}")
    return found


def test_only_the_policy_reads_the_sign_up_fields() -> None:
    found = {
        path.relative_to(SRC).as_posix(): reads
        for path in SRC.rglob("*.py")
        if (reads := _reads(path))
    }
    assert found == {}


def test_the_scan_sees_every_spelling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "classbeat"
    root.mkdir()
    (root / "x.py").write_text(
        "from operator import attrgetter\n"
        "from classbeat.flags import effective as flags\n"
        "a = cfg.signup_mode\n"
        "b = getattr(cfg, 'public_signup')\n"
        "c = attrgetter('signup_mode')(cfg)\n"
        "d = flags.public_signup\n"
        "e = flags.signup_mode(db, cfg)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "SRC", root)
    assert sorted(line.split(":")[0] for line in _reads(root / "x.py")) == ["3", "4", "5", "6"]


@pytest.mark.parametrize(
    ("public", "signup_mode", "turnstile", "environment", "expected"),
    [
        (True, "invite", False, "prod", "turnstile_off"),
        (True, "open", False, "prod", "turnstile_off"),
        (True, "invite", True, "prod", None),
        (True, "invite", False, "dev", None),
        (True, "closed", False, "prod", None),  # the brake is what the settings say
        (False, "invite", False, "prod", None),
    ],
)
def test_held_reason(
    public: bool, signup_mode: Any, turnstile: bool, environment: str, expected: str | None
) -> None:
    reason = held_reason(
        signup_mode=signup_mode,
        public_signup=public,
        turnstile_enabled=turnstile,
        environment=environment,
    )
    assert reason == expected
    fields = {
        "signup_mode": signup_mode,
        "public_signup": public,
        "turnstile_enabled": turnstile,
        "environment": environment,
    }
    assert environment_signup_held(SimpleNamespace(**fields)) == expected  # type: ignore[arg-type]


def _prod(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "prod",
        "public_base_url": "https://classbeat.example.test",
        "app_secret_key": "k" * 40,
        **overrides,
    }
    return Settings(**values)


@pytest.mark.parametrize("public", [True, False])
def test_a_held_sign_up_is_said_at_start_and_in_the_admin_view(
    public: bool, caplog: pytest.LogCaptureFixture
) -> None:
    from fastapi import FastAPI

    from classbeat.admin.settings import _flag  # pyright: ignore[reportPrivateUsage]
    from classbeat.auth.setup import attach_state, detach_state

    prod = _prod(public_signup=public)  # Turnstile off: the human degrade
    assert environment_signup_mode(prod) == "invite"
    app = FastAPI()
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.setup"):
        attach_state(app, prod, None)  # pyright: ignore[reportArgumentType]
    try:
        said = [r for r in caplog.records if "public sign-up is held at invite" in r.getMessage()]
        assert [r.levelno for r in said] == ([logging.WARNING] if public else [])
    finally:
        detach_state(app)
    view = _flag("signup_mode", prod, None, {}, {})
    assert (view.environment, view.environment_note) == (
        "invite",
        "turnstile_off" if public else None,
    )
