"""The render child's own memory watcher (``renderchild._watch``; #1425 review, non-blocking 5).

The parent (``studio.render``) kills a child over ``max_rss`` and is tested in
``test_studio_render_confinement``. The child also ends itself: ``_watch`` starts a thread that
reads this process's resident memory and exits with ``EXIT_FAILED`` the moment it passes the
limit, so a decode that balloons between two parent polls stops without writing more. These tests
run the child's code in a separate interpreter (``os._exit`` would end pytest) with a stand-in
``rss`` module, plus once with the real one. No network, no confinement.
"""

import subprocess
import sys

import pytest

from classbeat.studio import render

# ruff: noqa: S603

LIMIT = 1000

# Loads the child by path, as the parent runs it, then watches with the given stand-in ``rss``.
# ``process_rss`` answers only for this process's own pid, so watching another process fails.
PROBE = """
import importlib.util, os, sys, time, types
spec = importlib.util.spec_from_file_location("c", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
used = None if sys.argv[2] == "none" else int(sys.argv[2])
fake = types.SimpleNamespace(process_rss=lambda pid: used if pid == os.getpid() else None)
m._watch(fake, int(sys.argv[3]))
time.sleep(float(sys.argv[4]))
print("survived", flush=True)
"""


def probe(used: str, limit: int, sleep: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", PROBE, str(render.CHILD), used, str(limit), str(sleep)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_child_ends_itself_once_its_memory_passes_the_limit() -> None:
    done = probe(str(LIMIT + 1), LIMIT, sleep=10.0)
    assert done.returncode == 3  # EXIT_FAILED
    assert "survived" not in done.stdout  # it ended at once, not after its work


@pytest.mark.parametrize("used", [str(LIMIT), str(LIMIT - 1), "none"])
def test_the_child_runs_on_at_or_under_the_limit_or_when_memory_is_unknown(used: str) -> None:
    # 0.5 s is ten polls of the watcher: an exit would have come by then.
    done = probe(used, LIMIT, sleep=0.5)
    assert done.returncode == 0
    assert done.stdout == "survived\n"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc (the image)")
def test_the_child_ends_itself_with_the_real_memory_reader() -> None:
    # The real ``design/rss.py``, loaded by path as ``main`` does, with a limit the interpreter
    # alone already passes.
    code = (
        "import importlib.util, sys, time\n"
        "spec = importlib.util.spec_from_file_location('c', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m._watch(m.load(m.DESIGN, 'rss'), 1024 * 1024)\n"
        "time.sleep(10)\n"
        "print('survived', flush=True)\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code, str(render.CHILD)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 3
    assert "survived" not in done.stdout
