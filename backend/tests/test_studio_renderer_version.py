"""ADR 0042 section 6.4 (#1392): ``RENDERER_VERSION`` is part of a preview clip's cache key, so a
change to what the renderer writes must change it. The renderer is ``renderchild`` (decode, mix,
encode), ``render_curves`` (the transition curves) and ``render_policy`` (what a plan means). This
test hashes those three files and fails on a change that did not bump the version.

To change one of them: change the code, bump ``render.RENDERER_VERSION`` by one, and add the new
``(version, hashes)`` row to ``HISTORY`` (the failure message prints it). A row is never edited."""

import hashlib
from pathlib import Path

from classbeat.studio import render

STUDIO = Path(render.__file__).parent
FILES = ("renderchild.py", "render_curves.py", "render_policy.py")

# version -> sha256 of each file (line endings normalised to LF), oldest first.
HISTORY: dict[int, dict[str, str]] = {
    1: {
        "renderchild.py": ("3752276e6de36b07271e3877de505f9160ea0fff6f9af16eac5f79f0d9183d59"),
        "render_curves.py": ("ce86814fab58232975a8b085a2b85e545ea172c4eabfe2ed13b38cd39c106bc1"),
        "render_policy.py": ("821839b5da694cbaf7c2fdfde7f1d49c3d99db16ef47586d12a3cc0afaae00b9"),
    },
    2: {  # #1365: render_curves refuses overlapping swap regions of one track
        "renderchild.py": ("3752276e6de36b07271e3877de505f9160ea0fff6f9af16eac5f79f0d9183d59"),
        "render_curves.py": ("7401501255e138bbf4103cdb73de7377f8c15544dc5e65aaf0fc0fc925e3b7a6"),
        "render_policy.py": ("821839b5da694cbaf7c2fdfde7f1d49c3d99db16ef47586d12a3cc0afaae00b9"),
    },
    3: {  # #1425 follow-up: renderchild's docstring no longer names the deleted mix analysis
        "renderchild.py": ("e71209f4b22c7d6ef1980cfb941ffc9e8b9d62d2612847d7d02af91d0759820b"),
        "render_curves.py": ("7401501255e138bbf4103cdb73de7377f8c15544dc5e65aaf0fc0fc925e3b7a6"),
        "render_policy.py": ("821839b5da694cbaf7c2fdfde7f1d49c3d99db16ef47586d12a3cc0afaae00b9"),
    },
}


def hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((STUDIO / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in FILES
    }


def test_a_change_to_the_renderer_needs_a_new_version() -> None:
    current = hashes()
    version = render.RENDERER_VERSION
    hint = f"bump RENDERER_VERSION and add to HISTORY: {version + 1}: {current!r}"
    assert version == max(HISTORY), f"HISTORY is not at the current version; {hint}"
    assert current == HISTORY[version], f"the renderer changed without a new version; {hint}"


def test_the_history_is_a_run_of_versions_with_distinct_renderers() -> None:
    assert list(HISTORY) == list(range(1, len(HISTORY) + 1))
    rows = [tuple(sorted(row.items())) for row in HISTORY.values()]
    assert len(set(rows)) == len(rows)  # a version that changed nothing is not a version
    assert all(set(row) == set(FILES) for row in HISTORY.values())
