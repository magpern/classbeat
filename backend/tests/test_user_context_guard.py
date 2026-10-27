"""Guard 5 of ADR 0002 section 6, and the 'no raw user id' criterion of issue #5."""

from pathlib import Path

import pytest

from user_context_scan import DEFINITION, ISSUER_PACKAGES, scan_source

SRC = Path(__file__).resolve().parent.parent / "src"


def test_no_module_outside_the_issuers_builds_or_copies_a_user_context() -> None:
    violations = []
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative == DEFINITION or relative.startswith(ISSUER_PACKAGES):
            continue
        violations += [(relative, line, why) for line, why in scan_source(path.read_text("utf-8"))]
    assert violations == []


ALLOWED = [
    "from classbeat.context import UserContext\ndef f(ctx: UserContext) -> UserContext: ...",
    "from classbeat.context import UserContext\nasync def f(*, ctx: 'UserContext') -> None: ...",
    "from classbeat.context import UserContext\nx: list[UserContext] = []",
    "from __future__ import annotations\nfrom classbeat.context import UserContext\n"
    "def f(a: dict[str, UserContext | None]): ...",
    "import classbeat.context\ndef f(ctx: classbeat.context.UserContext): ...",
]
REFUSED = {
    "call": "from classbeat.context import UserContext\nctx = UserContext(user_id=1, role='user')",
    "call through an alias": "from classbeat.context import UserContext as C\nC(1, 'user')",
    "alias assignment": "from classbeat.context import UserContext\nC = UserContext\nC(1, 'user')",
    "module attribute call": "import classbeat.context as c\nc.UserContext(1, 'user')",
    "full path call": "import classbeat.context\nclassbeat.context.UserContext(1, 'user')",
    "subclass": "from classbeat.context import UserContext\nclass Mine(UserContext): ...",
    "isinstance": "from classbeat.context import UserContext\nisinstance(1, UserContext)",
    "cast": "from typing import cast\nfrom classbeat.context import UserContext\n"
    "cast(UserContext, 1)",
    "replace": "import dataclasses\nfrom classbeat.context import UserContext\n"
    "def f(ctx: UserContext): return dataclasses.replace(ctx, role='admin')",
    "replace imported": "from dataclasses import replace\n"
    "from classbeat.context import UserContext\n"
    "def f(ctx: UserContext): return replace(ctx, role='admin')",
    "partial": "import functools\nfrom classbeat.context import UserContext\n"
    "def f(ctx: UserContext): return functools.partial(print, ctx)",
    "partial of the class": "import functools\nfrom classbeat.context import UserContext\n"
    "p = functools.partial(UserContext, role='user')",
    "getattr": "import classbeat.context as m\ngetattr(m, 'UserContext')(1, 'user')",
    "star import": "from classbeat.context import *",
}


@pytest.mark.parametrize("source", ALLOWED)
def test_annotations_are_allowed(source: str) -> None:
    assert scan_source(source) == []


@pytest.mark.parametrize("source", REFUSED.values(), ids=REFUSED.keys())
def test_every_other_use_is_refused(source: str) -> None:
    assert scan_source(source) != []


def test_a_handler_cannot_pass_a_raw_user_id_to_a_session() -> None:
    # The only entry points take a UserContext; the runtime check is in test_isolation.py
    # (test_a_raw_user_id_is_not_a_context). Here: a handler that builds one is refused.
    handler = (
        "from fastapi import APIRouter\n"
        "from classbeat.context import UserContext\n"
        "from classbeat.db import user_session\n"
        "router = APIRouter()\n"
        "@router.get('/x/{user_id}')\n"
        "async def x(user_id: str, request):\n"
        "    ctx = UserContext(user_id=user_id, role='user')\n"
        "    async with user_session(request.app.state.session_factory, ctx): ...\n"
    )
    assert scan_source(handler) != []
