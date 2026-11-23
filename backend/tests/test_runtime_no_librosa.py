"""The runtime environment has no librosa or numba (#1358, ADR 0040 amendment item 3).

spotdown analyses every track and mix, so classbeat installs mowl-workouts without its
`analysis` extra: importing the worker, the web app or any other classbeat module loads none of
librosa, numba, llvmlite or scipy, and they are not installed. The render child's own libraries
(numpy, soundfile, soxr) are declared directly, no longer only through mowl-workouts.
"""

import ast
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ABSENT = ("librosa", "numba", "llvmlite", "scipy")
RENDER_LIBRARIES = ("numpy", "soundfile", "soxr")
PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"

# A fresh interpreter (`-I`, as the render tests run their child), so a module another test
# imported cannot hide or fake one. `renderchild` is skipped: it is run by path, never imported
# as part of the package.
IMPORT_EVERYTHING = f"""
import importlib, pkgutil, sys
import classbeat, classbeat.worker.__main__, classbeat.__main__, classbeat.web
failed = []
for m in pkgutil.walk_packages(classbeat.__path__, "classbeat."):
    if m.name.endswith(".renderchild"):
        continue
    try:
        importlib.import_module(m.name)
    except Exception as e:
        failed.append(m.name + ": " + type(e).__name__)
print(repr(failed))
print(repr(sorted(n for n in {ABSENT!r} if n in sys.modules)))
"""


def test_importing_the_worker_and_every_module_loads_no_librosa_or_numba() -> None:
    done = subprocess.run(  # noqa: S603 - our own interpreter, a fixed probe
        [sys.executable, "-I", "-c", IMPORT_EVERYTHING],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    failed, loaded = done.stdout.strip().splitlines()[-2:]
    assert failed == "[]"  # every module imported, so the check covered them all
    assert loaded == "[]"


@pytest.mark.parametrize("name", ABSENT)
def test_the_environment_does_not_install_it(name: str) -> None:
    assert importlib.util.find_spec(name) is None


def test_the_render_libraries_are_declared_directly() -> None:
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    reqs = [Requirement(d) for d in deps]
    pinned = {r.name: r for r in reqs if "==" in str(r.specifier)}
    for name in RENDER_LIBRARIES:
        assert name in pinned, f"{name} must be a direct, pinned dependency"
        assert importlib.util.find_spec(name) is not None
    assert not [r.name for r in reqs if canonicalize_name(r.name) in ABSENT]
    # never the `analysis` extra
    analysis = [r for r in reqs if canonicalize_name(r.name) == "mowl-workouts" and r.extras]
    assert not analysis


# --- no local analysis creeps back (ADR 0040 amendment "spotdown is the only analysis path") ------

SRC = Path(__file__).parents[1] / "src" / "classbeat"
# The library's own analysis entry points: classbeat never calls one (spotdown analyses every
# source), and ``mowl.music.require_audio_decoding`` would only work with the ``analysis`` extra.
LIBRARY_ANALYSIS = ("analyze_signal", "analyze_track", "require_audio_decoding")


def _source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_no_source_file_imports_an_analysis_library() -> None:
    offenders: list[str] = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in ABSENT]
    assert not offenders


def test_no_source_file_calls_the_librarys_analysis() -> None:
    offenders: list[str] = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called in LIBRARY_ANALYSIS:
                offenders.append(f"{path.name}: {called}")
    assert not offenders


def test_the_deleted_analysis_modules_stay_deleted() -> None:
    for name in ("design/mixanalysis.py", "design/mixchild.py", "design/track_shadow.py"):
        assert not (SRC / name).exists(), name
