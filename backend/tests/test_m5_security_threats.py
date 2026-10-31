"""M5 security suite (#227): the threat each security test covers, and the one threat with no
test of its own until now.

- Every test in ``test_m5_security_*.py`` names the threat of ``docs/security/threat-model.md``
  it covers (T85 to T96), in its name or its docstring, and every one of T85 to T96 is named by
  at least one test of the suite (some are covered by the issues' own test files: T85 to T87 by
  QT1's ``test_quota_*``, T92 by ``test_admin_audit.py``, T94 and T95 by ``test_flags*.py`` and
  ``test_admin_settings.py``).
- T93 (cross-site requests or caching of admin pages): every ``/api/admin`` write is refused by
  the CSRF check, without its token and from another origin, before the gate: 403 with no audit
  row and nothing changed; and an admin answer carries ``no-store`` and the CSP's
  ``frame-ancestors 'none'``.
"""

import ast
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import ORIGIN, Env, session_cookie
from test_m5_security_admin import (
    BODIES,
    admin,
    env,  # noqa: F401  (the fixture)
    fill,
    routes,
    target_with_entitlement,
)

TESTS = Path(__file__).resolve().parent
THREATS = [f"T{n}" for n in range(85, 97)]
NAMED = re.compile(r"(?<![A-Za-z0-9])[Tt](8[5-9]|9[0-6])(?![0-9])")


def _tests_of(path: Path) -> list[tuple[str, str]]:
    """``(name, docstring)`` of each test function in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.name, ast.get_docstring(node) or "")
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]


def _named(name: str, doc: str) -> set[str]:
    return {f"T{m.group(1)}" for m in NAMED.finditer(f"{name.replace('_', ' ')} {doc}")}


# --- every M5 security test names its threat --------------------------------------------------


def test_t85_to_t96_every_m5_security_test_names_the_threat_it_covers() -> None:
    """The #227 acceptance criterion, kept true: a new test in a ``test_m5_security_*`` file
    without a threat id fails here."""
    unnamed = [
        f"{path.name}::{name}"
        for path in sorted(TESTS.glob("test_m5_security_*.py"))
        for name, doc in _tests_of(path)
        if not _named(name, doc)
    ]
    assert unnamed == []


def test_t85_to_t96_each_threat_is_named_by_a_test_of_the_suite() -> None:
    named: set[str] = set()
    for path in TESTS.glob("test_*.py"):
        for name, doc in _tests_of(path):
            named |= _named(name, doc)
    assert [t for t in THREATS if t not in named] == []


def test_the_threat_ids_match_the_threat_model() -> None:
    """T85 to T96 are rows of the threat model marked M5, so the ids above mean what they say."""
    model = (TESTS.parents[1] / "docs" / "security" / "threat-model.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (T\d+) \|.*\| M5 \|\s*$", model, flags=re.MULTILINE)
    assert [t for t in THREATS if t not in rows] == []


# --- T93: CSRF on every admin write -----------------------------------------------------------


async def test_t93_every_admin_write_is_refused_without_its_csrf_token_or_from_another_origin(
    env: Env,  # noqa: F811
    make_actor: Any,
    superuser: Connection,
) -> None:
    actor = await admin(env, make_actor)
    values = await target_with_entitlement(env, superuser)
    before = superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one()
    writes = [(m, r) for m, r in routes(env) if m != "GET"]
    assert len(writes) >= 10  # the Users screen's writes, codes, refunds and settings at least
    for method, route in writes:
        path, body = fill(route, values), BODIES.get((method, route.path))
        for headers, why in (
            ({"Origin": ORIGIN}, "csrf_token_missing"),
            ({"Origin": ORIGIN, "X-CSRF-Token": "0" * 64}, "csrf_token_invalid"),
            ({"Origin": "https://evil.example"}, "origin_mismatch"),
        ):
            response = await actor.client.request(
                method, path, json=body, headers={**headers, **session_cookie(actor.token)}
            )
            actor.client.cookies.clear()
            where = f"{method} {route.path} {why}"
            assert response.status_code == 403, (where, response.text[:200])
            assert response.json() == {"detail": {"reason": why}}, where
    after = superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one()
    assert after == before  # refused before the gate: no row, so nothing ran
    kept = superuser.execute(
        text(
            "SELECT (SELECT disabled_at IS NULL FROM users WHERE id = :u), "
            "(SELECT count(*) FROM entitlements WHERE owner_id = :u AND revoked_at IS NULL), "
            "(SELECT state FROM credit_ledger WHERE id = :l), "
            "(SELECT count(*) FROM app_settings)"
        ),
        {"u": values["user_id"], "l": values["ledger_id"]},
    ).one()
    assert tuple(kept) == (True, 1, "committed", 0)


async def test_t93_an_admin_answer_is_never_cached_or_framed(env: Env, make_actor: Any) -> None:  # noqa: F811
    actor = await admin(env, make_actor)
    response = await actor.get("/api/admin/settings")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
