"""One vocabulary of tool error codes (ADR 0005 section 6, ADR 0006 section 3): what the agent
dispatcher can emit is exactly what a ``tool_called`` event may carry, through the real fenced
write and end to end with the FakeProvider scripts."""

import ast
from pathlib import Path
from typing import Any

import pytest

from agentfake import CONFIG, analysed, brief, provider
from classbeat.agent import DesignContext, JobStageHost, design_stage, run_stage
from classbeat.agent.dispatcher import ERROR_CODES
from classbeat.jobs import Complete, JobRun, fenced_session, queue
from classbeat.jobs.events import TOOL_ERROR_CODES
from classbeat.jobs.issuer import issue
from jobcore import KIND, JobsEnv, registry_for, run_next, store_draft

pytestmark = pytest.mark.usefixtures("jenv")

AGENT = Path(__file__).resolve().parent.parent / "src" / "classbeat" / "agent"
DISPATCHER_CODES = {
    "unknown_tool",
    "too_large",
    "invalid_input",
    "out_of_range",
    "invalid_draft",
    "result_too_large",
    "internal",
}


def emitted_codes() -> set[str]:
    """Every error code the agent package names in source: ``_error(call, tool, "code", ...)``
    in the dispatcher and ``ToolError("code", ...)`` anywhere."""
    found: set[str] = set()
    for path in sorted(AGENT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            index = {"ToolError": 0, "_error": 2}.get(getattr(node.func, "id", ""))
            if index is None or len(node.args) <= index:
                continue
            arg = node.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
    return found


def test_the_dispatcher_uses_the_one_vocabulary() -> None:
    assert frozenset(TOOL_ERROR_CODES) == ERROR_CODES
    assert len(set(TOOL_ERROR_CODES)) == len(TOOL_ERROR_CODES)


def test_every_code_the_agent_can_emit_is_one_an_event_may_carry() -> None:
    emitted = emitted_codes()
    # the scan itself works ('internal' is a fallback assigned in the dispatcher, not a literal)
    assert emitted >= DISPATCHER_CODES - {"internal"}
    assert emitted <= set(TOOL_ERROR_CODES)
    assert set(TOOL_ERROR_CODES) >= DISPATCHER_CODES


@pytest.mark.parametrize("code", [*TOOL_ERROR_CODES, None])
async def test_every_code_is_written_through_the_real_fenced_path(
    jenv: JobsEnv, code: str | None
) -> None:
    _, job_id = jenv.job()
    row = await queue.claim(jenv.factory, "test-w", jenv.settings.job_lease_seconds, [KIND])
    assert row is not None
    lease = (await issue(jenv.factory, row)).lease
    payload: dict[str, Any] = {"tool": "get_music", "ok": code is None}
    if code is not None:
        payload["error"] = code
    async with fenced_session(jenv.factory, lease) as fs:
        await fs.event("tool_called", payload)
    assert [p for _, k, p, _ in jenv.events(job_id) if k == "tool_called"] == [payload]


@pytest.mark.parametrize(
    ("script", "codes"),
    [
        ("out_of_range_index", {"out_of_range"}),
        ("oversized_input", {"too_large"}),
        ("fixed_after_validate", {"invalid_draft"}),
    ],
)
async def test_the_scripts_with_failing_tool_calls_complete_and_carry_the_codes(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch, script: str, codes: set[str]
) -> None:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    fake = provider(script)

    async def run(job_run: JobRun) -> Complete:
        await run_stage(JobStageHost(job_run), fake, design_stage(ctx), ctx.first_message(), CONFIG)
        return Complete(store_draft)

    _, job_id = jenv.job()
    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    tool_events = [p for _, k, p, _ in jenv.events(job_id) if k == "tool_called"]
    assert codes <= {p["error"] for p in tool_events if "error" in p}
