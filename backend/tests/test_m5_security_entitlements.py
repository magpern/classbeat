"""M5 security suite, entitlements (ADR 0011 sections 1, 2 and 10; SEC1, #227). Fakes only.

- T89: the app role cannot give a user a plan by direct SQL, through the real ``user_session``:
  no ``grant`` or ``subscription`` row, no change to an entitlement, a code's terms, a plan or a
  setting. Only ``classbeat.plans.redeem`` and ``classbeat.plans.trial`` call
  ``add_entitlement`` (an import guard, from the #252 review).
- T89: precedence. The rank decides, whatever the source (``subscription``, ``grant``, ``code``,
  ``trial``); a revoked, ended or not yet started entitlement stops applying on the user's next
  request, its features with it, in the same signed-in session.
- T88: a successful redeem uses up a rate-limit slot (from the #252 review), so ten good codes
  in an hour are the limit too.
"""

import ast
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from classbeat.db import user_session
from classbeat.plans.resolve import effective_plan
from ctx import make_context
from jobapi import Browser
from plantools import grant, plant_code, set_trial


@pytest.fixture
def users_plan() -> None:
    """These tests are about the plan itself: their users start on ``free`` (no grant)."""
    return None


PLAN = "/api/account/plan"
REDEEM = "/api/account/redeem"
SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"

# --- T89: no plan by direct SQL ---------------------------------------------------------------

POLICY_REFUSED = [
    "INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant')",
    "INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'subscription')",
    "INSERT INTO entitlements (owner_id, features, source) VALUES (:o, '{song_picking}', 'grant')",
]
NOT_GRANTED = [
    "UPDATE entitlements SET plan_id = 'pro' WHERE owner_id = :o",
    "UPDATE entitlements SET source = 'grant' WHERE owner_id = :o",
    "UPDATE entitlements SET ends_at = NULL WHERE owner_id = :o",
    "UPDATE entitlements SET revoked_at = NULL WHERE owner_id = :o",
    "UPDATE entitlements SET starts_at = now() - interval '1 day' WHERE owner_id = :o",
    "DELETE FROM entitlements WHERE owner_id = :o",
    "UPDATE redeem_codes SET plan_id = 'pro'",
    "UPDATE redeem_codes SET grant_days = 366",
    "UPDATE redeem_codes SET max_redemptions = 1000",
    "UPDATE redeem_codes SET expires_at = now() + interval '1 year'",
    "UPDATE redeem_codes SET disabled_at = NULL",
    "INSERT INTO redeem_codes (code_hash, label, plan_id, grant_days, max_redemptions, "
    "expires_at) VALUES (sha256('x'), 'mine', 'pro', 366, 1000, now() + interval '1 year')",
    "UPDATE plans SET classes_per_month = 10000 WHERE id = 'free'",
    "UPDATE plans SET rank = 99 WHERE id = 'free'",
    "INSERT INTO plans (id, name, rank, classes_per_month, revisions_per_class, concurrent_jobs, "
    "monthly_cost_micros) VALUES ('mine', 'Mine', 99, 10000, 200, 16, 0)",
    """INSERT INTO app_settings (key, value) VALUES ('trial', '{"plan": "pro", "days": 366}')""",
    """UPDATE app_settings SET value = '{"plan": "pro", "days": 366}'""",
]
DIRECT = [(s, "row-level security") for s in POLICY_REFUSED] + [
    (s, "permission denied") for s in NOT_GRANTED
]


@pytest.mark.parametrize(("statement", "refusal"), DIRECT)
async def test_t89_the_app_role_cannot_give_itself_a_plan_by_direct_sql(
    admin: Connection, browsers: Any, env: Any, statement: str, refusal: str
) -> None:
    alice: Browser = await browsers(mowl=False)
    code, _ = plant_code(admin, plan="instructor", days=1)
    assert (await alice.post(REDEEM, {"code": code})).status_code == 200  # a row to aim at
    ctx = make_context(alice.user_id)
    with pytest.raises(DBAPIError, match=refusal):
        async with user_session(env.factory, ctx) as db:
            await db.execute(text(statement), {"o": alice.user_id})
    async with user_session(env.factory, ctx) as db:
        assert (await effective_plan(db, ctx)).id == "instructor"
    held = admin.execute(
        text("SELECT source, plan_id FROM entitlements WHERE owner_id = :o"), {"o": alice.user_id}
    ).all()
    assert [tuple(r) for r in held] == [("code", "instructor")]
    assert admin.execute(text("SELECT count(*) FROM plans")).scalar_one() == 3


class _Calls(ast.NodeVisitor):
    """Every way a module can reach ``add_entitlement`` or its module."""

    def __init__(self) -> None:
        self.found: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.found += [a.name for a in node.names if a.name.startswith("classbeat.plans.entitle")]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module == "classbeat.plans.entitle":
            self.found += [f"{module}.{a.name}" for a in node.names if a.name == "add_entitlement"]
        elif module.startswith("classbeat.plans") and any(a.name == "entitle" for a in node.names):
            self.found.append(f"{module}.entitle")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "add_entitlement":
            self.found.append(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "add_entitlement":
            self.found.append(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and "add_entitlement" in node.value:
            self.found.append("string")


ENTITLING = {
    "classbeat.plans.entitle": "where it is defined",
    "classbeat.plans.redeem": "a code's entitlement (PL1)",
    "classbeat.plans.trial": "a trial's entitlement (PL1); it also imports plan_ranks",
}


def entitling_refs(source: str) -> list[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):  # docstrings may name it
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first: Any = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                first.value = ast.Constant(value=None)
    calls = _Calls()
    calls.visit(tree)
    return calls.found


def test_t89_only_redeem_and_trial_reach_add_entitlement() -> None:
    offenders = {}
    for path in SRC.rglob("*.py"):
        parts = path.relative_to(SRC.parent).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        found = entitling_refs(path.read_text(encoding="utf-8"))
        if found and module not in ENTITLING:
            offenders[module] = found
    assert offenders == {}
    # Admin grants go through classbeat_maint (AR1, AD1), never through the app's function.


@pytest.mark.parametrize(
    "source",
    [
        "from classbeat.plans.entitle import add_entitlement",
        "from classbeat.plans.entitle import add_entitlement as give",
        "import classbeat.plans.entitle",
        "import classbeat.plans.entitle as e",
        "from classbeat.plans import entitle",
        "x = entitle.add_entitlement",
        "getattr(m, 'add_entitlement')",
    ],
)
def test_the_entitlement_guard_sees_every_spelling(source: str) -> None:
    assert entitling_refs(source) != []


def test_the_entitlement_guard_ignores_docstrings_and_other_names() -> None:
    source = '"""Calls add_entitlement."""\nfrom classbeat.plans.entitle import plan_ranks\n'
    assert entitling_refs(source) == []


# --- T89: precedence and next-request revocation ----------------------------------------------


@pytest.fixture
def later(admin: Connection) -> Any:
    def at(interval: str) -> Any:
        return admin.execute(
            text("SELECT now() + CAST(:i AS interval)"), {"i": interval}
        ).scalar_one()

    return at


async def test_t89_the_rank_decides_whatever_the_source_and_revocation_applies_next_request(
    admin: Connection, browsers: Any, later: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    set_trial(admin, {"plan": "instructor", "days": 14})
    assert (await alice.post("/api/account/trial")).status_code == 200
    code, _ = plant_code(admin, plan="instructor", days=30)
    assert (await alice.post(REDEEM, {"code": code})).status_code == 200
    grant(admin, alice.user_id, "pro", ends_at=later("1 day"), features=["song_picking"])
    grant(admin, alice.user_id, "instructor", source="subscription")
    grant(admin, alice.user_id, None, source="subscription", starts_at=later("1 hour"),
          features=["song_picking"])  # fmt: skip
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["source"], body["features"]) == ("pro", "grant", ["song_picking"])

    # The admin revokes the grant (classbeat_maint sets revoked_at): the very next request, in
    # the same signed-in session, is on the best remaining plan, without the grant's feature.
    admin.execute(
        text("UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o AND source = 'grant'"),
        {"o": alice.user_id},
    )
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["source"], body["ends_at"], body["features"]) == (
        "instructor",
        "subscription",  # of the Instructor ones, the one without an end
        None,
        [],  # the subscription row with the feature has not started
    )
    assert body["limits"]["classes_per_month"] == 30

    # An ended subscription stops too; the code (30 days) outlasts the trial (14).
    admin.execute(
        text(
            "UPDATE entitlements SET starts_at = now() - interval '2 days', "
            "ends_at = now() - interval '1 second' WHERE owner_id = :o AND source = 'subscription'"
        ),
        {"o": alice.user_id},
    )
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["source"], body["features"]) == ("instructor", "code", [])

    admin.execute(
        text("UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o"),
        {"o": alice.user_id},
    )
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["source"], body["limits"]["classes_per_month"]) == ("free", None, 3)


async def test_t89_a_lower_ranked_entitlement_keeps_its_features_and_a_revoked_one_loses_them(
    admin: Connection, browsers: Any, later: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    grant(admin, alice.user_id, "pro", ends_at=later("1 day"))
    grant(admin, alice.user_id, None, features=["song_picking"], ends_at=later("1 day"))
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["features"]) == ("pro", ["song_picking"])
    admin.execute(
        text("UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o AND plan_id IS NULL"),
        {"o": alice.user_id},
    )
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["features"]) == ("pro", [])


# --- T88: a successful redeem uses a slot -----------------------------------------------------


async def test_t88_a_successful_redeem_uses_up_a_rate_limit_slot(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    for _ in range(10):
        code, _ = plant_code(admin, plan="instructor", days=1)
        assert (await alice.post(REDEEM, {"code": code})).status_code == 200
    code, code_id = plant_code(admin, plan="pro", days=1)
    limited = await alice.post(REDEEM, {"code": code})
    assert (limited.status_code, limited.json()) == (429, {"detail": {"reason": "rate_limited"}})
    assert (
        admin.execute(
            text("SELECT count(*) FROM entitlements WHERE code_id = :c"), {"c": code_id}
        ).scalar_one()
        == 0
    )
