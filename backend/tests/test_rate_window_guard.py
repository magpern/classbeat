"""#1012: a test that counts requests up to a fixed-window rate limit pins the window (#588).

The limiter keys its fixed windows off the database clock. A boundary inside a limit + 1 loop
restarts the count and the request that should be refused is let through (#995, #1010), so such
a test uses ``frozen_rate_windows`` (or ``freeze_rate_windows``, or a fixture of its own that
patches ``ratelimit._window_start``).

The rule is static and deliberately simple: a test function that mentions the status 429 is
"counting" and must be pinned, by ``pytestmark``, ``usefixtures``, a parameter,
or a parameter that is a fixture of the same file that patches ``_window_start``. It is an
allowlist: a file that is not about our fixed-window limiter is listed in ``EXEMPT`` with the
reason, so a new file that asserts a 429 fails here until its author decides. Limits: a 429 in a
helper (not in a test function) is not seen, and a limit-counting test that never mentions
429 is not seen (``max_count`` was tried as a second signal and also matches tests that only read
the limit table); review catches those. Tests of window rollover are not pinned on
purpose: their own file is exempt, or they leave the test unpinned and are not listed here.
"""

import ast
from pathlib import Path

PINS = frozenset({"frozen_rate_windows", "freeze_rate_windows"})
TESTS = Path(__file__).resolve().parent

PROVIDER = "a 429 from a faked MOWL, Spotify, Swedebeat, Paddle or model API, not our limiter"
BUSY = "429 busy: the plan's open-job count, not a time window"
PLANTED = "429 rows planted in the database; no request is counted"
OTHER = "not the database fixed-window limiter (a process-local or concurrency limit)"

EXEMPT: dict[str, str] = {
    "test_admin_abuse.py": PLANTED,
    "test_agent_anthropic.py": PROVIDER,
    "test_agent_openai_common.py": PROVIDER,
    "test_connection_errors.py": PROVIDER,
    "test_connections.py": PROVIDER,
    "test_design_with_songs_approve.py": BUSY,
    "test_interpret_job.py": BUSY,
    "test_jobs_api.py": BUSY,
    "test_jobs_api_approve.py": BUSY,
    "test_jobs_kind_seams.py": BUSY,
    "test_jobs_stream.py": OTHER + ": the open event streams per user",
    "test_m5_done_when.py": BUSY,
    "test_migrations.py": PLANTED,
    "test_mowl_connect.py": PROVIDER,
    "test_mowl_qr.py": PROVIDER,
    "test_mowl_redaction.py": PROVIDER,
    "test_operations_api.py": PROVIDER,
    "test_paddle_webhook.py": OTHER + ": WebhookLimiter keeps its window in process memory",
    "test_quota_api.py": BUSY,
    "test_spotify_errors.py": PROVIDER,
    "test_spotify_fake.py": PROVIDER,
    "test_swedebeat_attach.py": PROVIDER,
    "test_swedebeat_fakes.py": PROVIDER,
    "test_swedebeat_fetch.py": PROVIDER,
    "test_swedebeat_match.py": PROVIDER,
    "test_swedebeat_match_catalogue.py": PROVIDER,
    "test_swedebeat_media_fetch.py": PROVIDER,
    "test_turnstile_verify.py": PROVIDER,
}
# PR #1011 pins these two; delete this set (and its two uses) when it merges.
PENDING_1011 = frozenset({"test_auth_mfa_hardening.py", "test_two_workers.py"})


def _names(nodes: list[ast.expr]) -> set[str]:
    """The string constants of ``usefixtures("a", "b")`` calls among decorator/assign nodes."""
    found: set[str] = set()
    for node in nodes:
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and "usefixtures" in ast.unparse(call.func):
                found |= {
                    a.value
                    for a in call.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                }
    return found


def _counts(func: ast.AST) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == 429 for n in ast.walk(func))


def unpinned_counting_tests(source: str) -> list[str]:
    """Names of the test functions in ``source`` that count to a limit without a window pin."""
    tree = ast.parse(source)
    marks = [
        n.value for n in tree.body if isinstance(n, ast.Assign) and _is_pytestmark(n) and n.value
    ]
    pinned = set(PINS) if _names(marks) & PINS else set()
    own = {  # fixtures of this file that patch the window themselves
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        and "_window_start" in ast.unparse(fn)
    }
    bad = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not fn.name.startswith("test") or not _counts(fn):
            continue
        params = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs}
        here = _names(list(fn.decorator_list)) | params
        if not (pinned or here & (PINS | own)):
            bad.append(fn.name)
    return bad


def _is_pytestmark(node: ast.Assign) -> bool:
    return any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)


def test_every_counting_test_pins_the_rate_windows() -> None:
    found = {}
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name in EXEMPT or path.name in PENDING_1011:
            continue
        bad = unpinned_counting_tests(path.read_text("utf-8"))
        if bad:
            found[path.name] = bad
    assert found == {}, 'add pytest.mark.usefixtures("frozen_rate_windows") (#588, #1012)'


def test_an_exemption_is_still_needed() -> None:
    """A file leaves ``EXEMPT`` once it pins its windows or no longer counts."""
    stale = []
    for name in EXEMPT:
        path = TESTS / name
        source = path.read_text("utf-8") if path.exists() else ""
        if not source or not _counts(ast.parse(source)) or not unpinned_counting_tests(source):
            stale.append(name)
    assert stale == []


def test_the_scan_sees_what_it_should() -> None:
    plain = "async def test_x(env):\n    assert r.status_code == 429\n"
    assert unpinned_counting_tests(plain) == ["test_x"]
    assert unpinned_counting_tests("def test_x():\n    assert code == 200\n") == []
    param = "async def test_x(env, frozen_rate_windows):\n    assert r == 429\n"
    assert unpinned_counting_tests(param) == []
    deco = '@pytest.mark.usefixtures("a", "frozen_rate_windows")\ndef test_x():\n    x = 429\n'
    assert unpinned_counting_tests(deco) == []
    mark = 'pytestmark = [pytest.mark.usefixtures("frozen_rate_windows")]\ndef test_x():\n    429\n'
    assert unpinned_counting_tests(mark) == []
    own = (
        "def one_minute(monkeypatch):\n    monkeypatch.setattr(ratelimit, '_window_start', f)\n"
        "def test_x(one_minute):\n    429\n"
    )
    assert unpinned_counting_tests(own) == []
    assert unpinned_counting_tests(own.replace("(one_minute)", "()")) == ["test_x"]
