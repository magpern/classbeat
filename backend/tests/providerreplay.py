"""The replay transport for the OpenAI-SDK providers (ADR 0012 §5, "How the gate is tested").

``Replay`` is the ``inner`` transport of ``openai_common.build_client`` (an
``httpx.MockTransport`` under the provider's one-host allowlist, so the allowlist check still
runs). It answers each request with the next recorded ``Answer`` (inline, or a JSON file under
``tests/provider_fixtures/<provider>/`` holding ``status``, optional ``headers`` and ``body``) and
checks every request:

- ``https`` to the provider's one host on the default port, the answer's method and path;
- the test key as ``Authorization: Bearer``, and no organisation or project header;
- a JSON object body (when one is sent) that the answer's ``check`` accepts (the body shape);
- no planted value anywhere in the URL, the headers or the body.

A mismatch is recorded in ``problems`` and raised as ``ReplayMismatch``, an ``openai.OpenAIError``,
so the SDK passes it through without a retry rather than hiding it as a connection error. Tests
assert ``replay.problems == []``. Nothing here touches the network.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import openai

from classbeat.egress import MODEL_HOSTS

FIXTURES = Path(__file__).resolve().parent / "provider_fixtures"
# A test key only: never a real key, and never sent anywhere but the mock transport.
TEST_KEY = "sk-test-replay-not-a-key-0123456789"


class ReplayMismatch(openai.OpenAIError):
    """A request did not match the recorded exchange (see ``Replay.problems``)."""


@dataclass(frozen=True)
class Answer:
    path: str
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict[str, str])
    method: str = "POST"
    check: Callable[[dict[str, Any]], bool] | None = None  # the request body's shape

    @classmethod
    def from_file(
        cls, provider: str, name: str, *, path: str, method: str = "POST", **kw: Any
    ) -> "Answer":
        data = json.loads((FIXTURES / provider / f"{name}.json").read_text(encoding="utf-8"))
        return cls(
            path=path,
            method=method,
            status=int(data.get("status", 200)),
            body=data.get("body"),
            headers=dict(data.get("headers", {})),
            **kw,
        )


class Replay:
    def __init__(
        self,
        provider: str,
        answers: Sequence[Answer],
        *,
        key: str = TEST_KEY,
        planted: Sequence[str] = (),
    ) -> None:
        self.host = MODEL_HOSTS[provider]  # pyright: ignore[reportArgumentType]
        self.answers = list(answers)
        self.key = key
        self.planted = tuple(planted)
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []
        self.problems: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def _problem(self, text: str) -> ReplayMismatch:
        self.problems.append(text)
        return ReplayMismatch(text)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        n = len(self.requests)
        if n > len(self.answers):
            raise self._problem(f"request {n}: no recorded answer left")
        answer = self.answers[n - 1]
        body = self.checked(request, n, answer.method, answer.path)
        if answer.check is not None and not answer.check(body or {}):
            raise self._problem(f"request {n}: the body shape is not the expected one")
        return httpx.Response(answer.status, json=answer.body, headers=answer.headers)

    def checked(self, request: httpx.Request, n: int, method: str, path: str) -> Any:
        """Every check above but the body's shape; the parsed body (also kept in ``bodies``)."""
        url = request.url
        if (url.scheme, url.host, url.port) != ("https", self.host, None):
            raise self._problem(f"request {n}: not https to {self.host}")
        if (request.method, url.path) != (method, path):
            raise self._problem(f"request {n}: {request.method} {url.path}, not {path}")
        if request.headers.get("authorization") != f"Bearer {self.key}":
            raise self._problem(f"request {n}: not the test key")
        for name in ("openai-organization", "openai-project"):
            if name in request.headers:
                raise self._problem(f"request {n}: sends {name}")
        raw = request.content.decode("utf-8", "replace")
        seen = " ".join([str(url), raw, *(f"{k}: {v}" for k, v in request.headers.items())])
        for value in self.planted:
            if value in seen:
                raise self._problem(f"request {n}: a planted value went out")
        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                raise self._problem(f"request {n}: the body is not JSON") from None
            if not isinstance(body, dict):
                raise self._problem(f"request {n}: the body is not a JSON object")
        self.bodies.append(body)
        return body
