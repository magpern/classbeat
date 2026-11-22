"""The runtime environment has no librosa or numba (#1358, ADR 0040 amendment item 3).

spotdown analyses every track and mix, so classbeat installs mowl-workouts without its
`analysis` extra: importing the worker, the web app or any other classbeat module loads none of
librosa, numba, llvmlite or scipy, and they are not installed. The render child's own libraries
(numpy, soundfile, soxr) are declared directly, no longer only through mowl-workouts.
"""

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

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
    pinned = {d.split("==")[0]: d for d in deps if "==" in d}
    for name in RENDER_LIBRARIES:
        assert name in pinned, f"{name} must be a direct, pinned dependency"
        assert importlib.util.find_spec(name) is not None
    assert not [d for d in deps if d.split("=")[0].split("[")[0].strip() in ABSENT]
    assert "mowl-workouts[" not in " ".join(deps)  # never the `analysis` extra
