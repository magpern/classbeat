"""Guard 5 of ADR 0002 section 6, and the 'no raw user id' criterion of issue #5."""

from pathlib import Path

import pytest

from user_context_scan import DEFINITION, ISSUER_PACKAGES, scan_source

DEPENDS = "from fastapi import Depends"
ANNOTATED = "from typing import Annotated"


def lines(*parts: str) -> str:
    return chr(10).join(parts)


SRC = Path(__file__).resolve().parent.parent / "src"
IMPORT = "from classbeat.context import UserContext\n"


def test_no_module_outside_the_issuers_builds_or_copies_a_user_context() -> None:
    violations = []
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative == DEFINITION or relative.startswith(ISSUER_PACKAGES):
            continue
        violations += [(relative, line, why) for line, why in scan_source(path.read_text("utf-8"))]
    assert violations == []


ALLOWED = [
    IMPORT + "def f(ctx: UserContext) -> UserContext: ...",
    IMPORT + "async def f(*, ctx: 'UserContext') -> None: ...",
    IMPORT + "x: list[UserContext] = []",
    "from __future__ import annotations\n"
    + IMPORT
    + "def f(a: dict[str, UserContext | None]): ...",
    "import classbeat.context\ndef f(ctx: classbeat.context.UserContext): ...",
    "import functools\np = functools.partial(print, 1)",  # no UserContext in this module
    "x = 'a'.replace('a', 'b')",
    lines(DEPENDS, IMPORT, "def get(): ...", "async def h(ctx: UserContext = Depends(get)): ..."),
    lines(
        ANNOTATED,
        DEPENDS,
        IMPORT,
        "def get(): ...",
        "async def h(ctx: Annotated[UserContext, Depends(get)]): ...",
    ),
    lines(DEPENDS, "def h(x: int = Depends()): ..."),
    lines(ANNOTATED, DEPENDS, IMPORT, "def h(x: Annotated[int, Depends()]): ..."),
]
REFUSED = {
    "call": IMPORT + "ctx = UserContext(user_id=1, role='user')",
    "call through an alias": "from classbeat.context import UserContext as C\nC(1, 'user')",
    "alias assignment": IMPORT + "C = UserContext\nC(1, 'user')",
    "module attribute call": "import classbeat.context as c\nc.UserContext(1, 'user')",
    "full path call": "import classbeat.context\nclassbeat.context.UserContext(1, 'user')",
    "subclass": IMPORT + "class Mine(UserContext): ...",
    "isinstance": IMPORT + "isinstance(1, UserContext)",
    "cast": "from typing import cast\n" + IMPORT + "cast(UserContext, 1)",
    "replace": "import dataclasses\n"
    + IMPORT
    + "def f(ctx: UserContext): return dataclasses.replace(ctx, role='admin')",
    "partial": "import functools\n"
    + IMPORT
    + "def f(ctx: UserContext): return functools.partial(print, ctx)",
    "partial of the class": "import functools\n"
    + IMPORT
    + "p = functools.partial(UserContext, role='user')",
    "getattr": "import classbeat.context as m\ngetattr(m, 'UserContext')(1, 'user')",
    "star import": "from classbeat.context import *",
    "type(x)(...)": IMPORT + "def f(ctx: UserContext): return type(ctx)(user_id=1, role='admin')",
    "type(x)(...) in a module that never names the class": "def f(ctx): return type(ctx)(1, 'a')",
    "x.__class__(...)": "def f(ctx): return ctx.__class__(1, 'admin')",
    "object.__setattr__": "def f(ctx): object.__setattr__(ctx, 'user_id', 1)",
    "object.__new__": "def f(cls): return object.__new__(cls)",
    "replace without naming the class": "import dataclasses\n"
    "def f(ctx): return dataclasses.replace(ctx, user_id=1)",
    "replace imported without naming the class": "from dataclasses import replace\n"
    "def f(ctx): return replace(ctx, user_id=1)",
    "replace imported with an alias": "from dataclasses import replace as r\n"
    "def f(ctx): return r(ctx)",
    "issuer token import": "from classbeat.context import issuer_token\nissuer_token()",
    "issuer token attribute": "import classbeat.context as c\nc.issuer_token()",
    "Depends(UserContext)": lines(DEPENDS, IMPORT, "async def h(ctx=Depends(UserContext)): ..."),
    "Depends(UserContext) in Annotated": lines(
        ANNOTATED,
        DEPENDS,
        IMPORT,
        "async def h(ctx: Annotated[UserContext, Depends(UserContext)]): ...",
    ),
    "Depends(UserContext) through an alias": lines(
        "import fastapi",
        "from classbeat.context import UserContext as C",
        "async def h(ctx=fastapi.Depends(dependency=C)): ...",
    ),
    "bare Depends() default": lines(
        DEPENDS, IMPORT, "async def h(ctx: UserContext = Depends()): ..."
    ),
    "bare Depends() keyword-only default": lines(
        DEPENDS, IMPORT, "async def h(*, ctx: UserContext = Depends()): ..."
    ),
    "bare Depends() in Annotated": lines(
        ANNOTATED, DEPENDS, IMPORT, "async def h(ctx: Annotated[UserContext, Depends()]): ..."
    ),
    "bare Depends() in typing.Annotated": lines(
        "import typing",
        DEPENDS,
        IMPORT,
        "async def h(ctx: typing.Annotated[UserContext, Depends()]): ...",
    ),
    "bare Depends() with a string annotation": lines(
        DEPENDS, IMPORT, "async def h(ctx: 'UserContext' = Depends()): ..."
    ),
    "issuer private token": "from classbeat.context import _ISSUER\nx = _ISSUER",
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
        "from fastapi import APIRouter\n" + IMPORT + "from classbeat.db import user_session\n"
        "router = APIRouter()\n"
        "@router.get('/x/{user_id}')\n"
        "async def x(user_id: str, request):\n"
        "    ctx = UserContext(user_id=user_id, role='user')\n"
        "    async with user_session(request.app.state.session_factory, ctx): ...\n"
    )
    assert scan_source(handler) != []
