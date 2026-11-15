"""Who can create the send and the removal, and what a press binds (ADR 0034 section 5, #1060;
the product rule "model output is a proposal; the backend authorizes every write").

- **No model output can create either job.** Only an allowlist of modules imports
  ``classbeat.player_send``, and only an allowlist of modules names either kind at all: the
  agent, design, songs and tool modules are in neither. The jobs API refuses ``player_remove``
  (``unknown_kind``: its input is no ``JobInput``); only the class delete inserts one.
- **The revision and its content hash are bound to the press.** The API refuses a wrong hash, a
  revision of another class and a disconnected player, with nothing stored; the worker sends the
  pressed revision even when a newer one exists, refuses a job whose hash was changed after the
  press, and never sends a retry with another document than its first attempt's.

Against Postgres as ``classbeat_app``, the real jobs API and the fake player. Never the network.
"""

import ast
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from authtools import ORIGIN, Env
from classbeat.player_class_calc import player_class
from classbeat.player_format_policy import Converted, convert
from jobapi import Browser, new_key
from playersendtools import Planted, SendEnv

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"
# Every module that imports ``classbeat.player_send`` (or a module of it): the package itself,
# the class delete (the removal), and the two processes that register the kinds.
IMPORTERS = {"classes/service.py", "web.py", "worker/__main__.py"}
# Every module outside the package that names a kind: the policy, the kinds' brief
# classification, the operation registry and its checks, the connection's data and the jobs
# API's ``class_id`` comment.
NAMERS = IMPORTERS | {
    "player_publish_policy.py",
    "classes/kinds.py",  # the brief classification of every kind (test_class_kinds.py)
    "operations/kinds.py",
    "operations/orphans.py",
    "connections/player_data.py",
    "jobs/routes.py",
    "jobs/service.py",
}
KIND_NAMES = ("send_to_player", "player_remove", "player_send")


def modules() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        name = path.relative_to(SRC).as_posix()
        if not name.startswith("player_send/"):
            found.append((name, path.read_text(encoding="utf-8")))
    return found


def imports_player_send(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "classbeat.player_send" or node.module.startswith(
                "classbeat.player_send."
            ):
                return True
            if node.module == "classbeat" and any(a.name == "player_send" for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(
            a.name.startswith("classbeat.player_send") for a in node.names
        ):
            return True
    return False


def test_only_the_allowlisted_modules_import_the_player_send_package() -> None:
    importers = {name for name, source in modules() if imports_player_send(source)}
    assert importers == IMPORTERS


def test_only_the_allowlisted_modules_name_either_kind() -> None:
    namers = {name for name, source in modules() if any(k in source for k in KIND_NAMES)}
    assert namers <= NAMERS, namers - NAMERS
    for prefix in ("agent/", "design/", "songs/", "tools/", "models/"):
        assert not any(n.startswith(prefix) for n in namers)


@pytest.fixture
async def browser_of(env: Env, sendenv: SendEnv) -> AsyncIterator[Any]:
    clients: list[httpx.AsyncClient] = []

    async def make(planted: Planted) -> Browser:
        issued = await env.login(planted.owner)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN)
        clients.append(client)
        return Browser(env, issued.token, client, planted.owner)

    yield make
    for client in clients:
        await client.aclose()


async def create(browser: Browser, kind: str, given: dict[str, Any]) -> httpx.Response:
    body = {"kind": kind, "input": given}
    return await browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


def document_hash(content: dict[str, Any]) -> str:
    converted = convert(player_class(content, "cycling", "none", 0, ()))
    assert isinstance(converted, Converted)
    return converted.content_hash


def job_count(sendenv: SendEnv, planted: Planted) -> int:
    return sendenv.sql(
        "SELECT count(*) FROM jobs WHERE owner_id = :o", o=planted.owner
    ).scalar_one()


async def connected(sendenv: SendEnv) -> Planted:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    return sendenv.plant(owner)


async def test_the_api_never_creates_a_removal(sendenv: SendEnv, browser_of: Any) -> None:
    planted = await connected(sendenv)
    browser = await browser_of(planted)
    answer = await create(
        browser, "player_remove", {"source_class_id": str(planted.class_id), "number": 2}
    )
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (422, "unknown_kind")
    assert job_count(sendenv, planted) == 0


@pytest.mark.parametrize(
    ("reason", "status", "change"),
    [
        ("hash_mismatch", 409, {"content_hash": "0" * 64}),
        ("invalid_input", 409, "other_class"),
        ("not_connected", 409, "disconnected"),
    ],
)
async def test_the_api_refuses_a_press_that_does_not_bind(
    sendenv: SendEnv, browser_of: Any, reason: str, status: int, change: Any
) -> None:
    planted = await connected(sendenv)
    browser = await browser_of(planted)
    given = planted.input()
    if change == "other_class":
        other = sendenv.plant(planted.owner)
        given = planted.input(revision_id=str(other.revision_id))
    elif change == "disconnected":
        sendenv.set_status(planted.owner, "needs_reconnect")
    else:
        given = planted.input(**change)
    answer = await create(browser, "send_to_player", given)
    assert answer.status_code == status, answer.text
    assert answer.json()["detail"]["reason"] == reason
    assert job_count(sendenv, planted) == 0


async def test_the_api_accepts_a_press_and_the_worker_sends_that_revision(
    sendenv: SendEnv, browser_of: Any
) -> None:
    planted = await connected(sendenv)
    browser = await browser_of(planted)
    answer = await create(browser, "send_to_player", planted.input())
    assert answer.status_code == 201, answer.text
    # A newer revision appears after the press: the pressed one is still what is sent.
    newer = {**planted.content, "name": "Thursday climb"}
    sendenv.revision(planted.owner, planted.class_id, newer)
    await sendenv.run_next()
    [body] = sendenv.fake.posts
    assert body["content_hash"] == document_hash(planted.content)
    assert body["content_hash"] != document_hash(newer)
    row = sendenv.publication(planted.owner, planted.class_id)
    assert row.sent_revision_id == planted.revision_id


async def test_a_hash_changed_after_the_press_is_refused_by_the_worker(sendenv: SendEnv) -> None:
    planted = await connected(sendenv)
    job_id = sendenv.job(planted, content_hash="1" * 64)
    await sendenv.run_next()
    assert (sendenv.row(job_id).state, sendenv.row(job_id).error_category) == (
        "failed",
        "invalid_input",
    )
    assert sendenv.fake.posts == []


async def test_a_retry_never_sends_another_document(sendenv: SendEnv) -> None:
    planted = await connected(sendenv)
    sendenv.fake.script = ["refuse"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    # The first attempt's operation names another document than the revision converts to now.
    sendenv.sql(
        "UPDATE external_operations SET request_context = jsonb_set(request_context, "
        "'{document_hash}', CAST(:h AS jsonb)) WHERE owner_id = :o AND kind = 'player_send'",
        h=json.dumps("sha256:" + "2" * 64), o=planted.owner,
    )  # fmt: skip
    sendenv.ready(job_id)
    await sendenv.run_next()
    assert (sendenv.row(job_id).state, sendenv.row(job_id).error_category) == (
        "failed",
        "invalid_input",
    )
    assert len(sendenv.fake.posts) == 1
