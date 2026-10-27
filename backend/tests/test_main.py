from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

from classbeat.__main__ import UVICORN_LOOP, main


@pytest.mark.usefixtures("restore_logging")
def test_main_runs_uvicorn_with_the_json_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    main(["--host", "0.0.0.0", "--port", "9000"])  # noqa: S104 - what the container passes
    ((app, kwargs),) = calls
    assert isinstance(app, FastAPI)
    assert kwargs == {"host": "0.0.0.0", "port": 9000, "log_config": None, "loop": UVICORN_LOOP}  # noqa: S104


@pytest.mark.usefixtures("restore_logging")
def test_main_defaults_to_localhost_8000(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    main([])
    assert calls == [{"host": "127.0.0.1", "port": 8000, "log_config": None, "loop": UVICORN_LOOP}]
