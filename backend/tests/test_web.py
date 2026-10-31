import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from authtools import APP_SECRET
from classbeat import __version__
from classbeat.config import Settings
from classbeat.db import DatabaseRoleError
from classbeat.web import create_app
from dbtools import migrated


def make_app(database_url: str, **overrides: object) -> FastAPI:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": database_url,
        "app_secret_key": APP_SECRET,
        **overrides,
    }
    return create_app(Settings(**values))  # pyright: ignore[reportArgumentType]


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """A client for ``app`` with its lifespan (engine creation and disposal) running."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def make_client(database_url: str, **overrides: object):
    return running(make_app(database_url, **overrides))


@pytest.fixture
async def down_client(unreachable_database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """A client for an app whose database cannot be reached."""
    async with make_client(unreachable_database_url) as client:
        yield client


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>classbeat</title>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('app');", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the static dir", encoding="utf-8")
    return root


async def test_health_does_not_touch_the_database(unreachable_database_url: str) -> None:
    def no_database(*_: object) -> NoReturn:
        raise AssertionError("/api/health must not touch the database")

    app = make_app(unreachable_database_url)
    async with running(app) as client:
        app.state.session_factory = no_database
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


@pytest.fixture
def schema(owner_database_url: str) -> Iterator[None]:
    """The migrated schema: the KEK check of ADR 0004 reads ``connection_keks`` at startup."""
    with migrated(owner_database_url):
        yield


@pytest.mark.usefixtures("schema")
async def test_ready_is_200_with_a_real_database(app_database_url: str) -> None:
    async with make_client(app_database_url) as client:
        response = await client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.usefixtures("schema")
async def test_ready_uses_database_password_instead_of_the_url_password(
    app_database_url: str,
) -> None:
    url = make_url(app_database_url)
    assert url.password, "the test database URL must carry a password"
    wrong = url.set(password="wrong-" + url.password).render_as_string(hide_password=False)
    # A refused login is a fatal startup error now (ADR 0002 section 4), not a 503.
    with pytest.raises(DatabaseRoleError, match="refused the configured credentials"):
        async with make_client(wrong):
            pytest.fail("the app must not start")
    async with make_client(wrong, database_password=url.password) as client:
        assert (await client.get("/api/ready")).status_code == 200


async def test_ready_is_503_when_the_database_is_unreachable(
    down_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="classbeat.web"):
        response = await down_client.get("/api/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}  # no host, user or error text
    assert "not-a-real-password" not in caplog.text
    assert any(record.getMessage() == "database not ready" for record in caplog.records)


async def test_unknown_api_path_is_a_json_404(down_client: httpx.AsyncClient) -> None:
    response = await down_client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Not Found"}


async def test_api_docs_live_under_api_and_are_off_in_prod(
    unreachable_database_url: str,
) -> None:
    async with make_client(unreachable_database_url) as client:
        assert (await client.get("/api/openapi.json")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 404
        assert (await client.get("/docs")).status_code == 404
    async with make_client(
        unreachable_database_url, environment="prod", public_base_url="https://classbeat.example"
    ) as client:
        assert (await client.get("/api/openapi.json")).status_code == 404
        assert (await client.get("/api/docs")).status_code == 404


async def test_without_static_dir_other_paths_are_404(down_client: httpx.AsyncClient) -> None:
    assert (await down_client.get("/")).status_code == 404
    assert (await down_client.get("/classes/1")).status_code == 404


async def test_serves_static_files_and_the_spa_fallback(
    unreachable_database_url: str, static_dir: Path
) -> None:
    index = (static_dir / "index.html").read_text(encoding="utf-8")
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        asset = await client.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('app');"

        for path in ("/", "/classes/42", "/assets/missing.js", "/index.html"):
            page = await client.get(path)
            assert page.status_code == 200, path
            assert page.text == index, path
            assert page.headers["content-type"].startswith("text/html"), path


async def test_api_paths_are_never_answered_by_the_spa_fallback(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/api", "/api/", "/api/nope", "/api/health/extra"):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-type"] == "application/json", path
        assert (await client.get("/api/health")).json()["status"] == "ok"


async def test_static_files_cannot_escape_the_static_dir(
    unreachable_database_url: str, static_dir: Path
) -> None:
    index = (static_dir / "index.html").read_text(encoding="utf-8")
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/..%2f..%2fsecret.txt"):
            response = await client.get(path)
            assert "outside the static dir" not in response.text, path
            assert response.text == index, path


async def test_a_missing_static_dir_is_logged_and_ignored(
    unreachable_database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="classbeat.web"):
        app = make_app(unreachable_database_url, static_dir=tmp_path / "missing")
    assert any("static_dir does not exist" in record.getMessage() for record in caplog.records)
    async with running(app) as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/")).status_code == 404


async def test_static_dir_without_index_html_is_a_404_not_an_error(
    unreachable_database_url: str, tmp_path: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=tmp_path) as client:
        assert (await client.get("/classes/1")).status_code == 404


async def test_the_app_refuses_to_start_without_the_app_secret_key(
    unreachable_database_url: str,
) -> None:
    app = create_app(
        Settings(environment="test", database_url=unreachable_database_url)  # pyright: ignore[reportArgumentType]
    )
    with pytest.raises(RuntimeError, match="app_secret_key"):
        async with running(app):
            pytest.fail("the app must not start")


# --- the security headers (ADR 0013 section 9, issue #251) ------------------------------------

CSP_EXPECTED = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'"],
    "img-src": ["'self'", "https://i.scdn.co"],
    "font-src": ["'self'"],
    "connect-src": ["'self'"],
    "media-src": ["'none'"],
    "object-src": ["'none'"],
    "frame-src": ["'none'"],
    "worker-src": ["'none'"],
    "manifest-src": ["'self'"],
    "base-uri": ["'none'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
}


def csp_of(response: httpx.Response) -> dict[str, list[str]]:
    assert len(response.headers.get_list("content-security-policy")) == 1  # one policy, no merge
    directives: dict[str, list[str]] = {}
    for part in response.headers["content-security-policy"].split(";"):
        name, *values = part.split()
        assert name not in directives, name
        directives[name] = values
    return directives


def assert_secure_headers(response: httpx.Response, path: str) -> None:
    assert csp_of(response) == CSP_EXPECTED, path
    assert response.headers.get_list("referrer-policy") == ["no-referrer"], path


async def test_every_response_carries_the_csp_and_no_referrer(
    unreachable_database_url: str, static_dir: Path
) -> None:
    """The SPA page, its files and the fallback, the API (200, 404 and 503) and a 401 from the
    auth layer all carry the one policy: images only from us and the pinned cover host, nothing
    inline, no framing, and no referrer."""
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/", "/classes/42", "/assets/app.js", "/index.html"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert_secure_headers(response, path)
        for path, status in (
            ("/api/health", 200),
            ("/api/ready", 503),
            ("/api/nope", 404),
            ("/api/classes", 401),
        ):
            response = await client.get(path)
            assert response.status_code == status, path
            assert_secure_headers(response, path)


async def test_an_unhandled_500_carries_the_csp_and_no_referrer(
    unreachable_database_url: str,
) -> None:
    """Starlette answers an unhandled exception outside our middleware (T93): the 500 must
    still carry the one policy and no referrer, and say nothing about the error."""
    app = make_app(unreachable_database_url)

    @app.get("/api/boom-for-test")
    async def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError("secret internal detail")

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/boom-for-test")
    assert response.status_code == 500
    assert "secret" not in response.text
    assert_secure_headers(response, "/api/boom-for-test")


def test_the_policy_allows_no_other_image_host_and_nothing_unsafe() -> None:
    from classbeat.headers import CONTENT_SECURITY_POLICY

    assert "unsafe" not in CONTENT_SECURITY_POLICY
    assert "data:" not in CONTENT_SECURITY_POLICY
    assert "*" not in CONTENT_SECURITY_POLICY
    assert CONTENT_SECURITY_POLICY.count("https://") == 1
    assert "img-src 'self' https://i.scdn.co;" in CONTENT_SECURITY_POLICY
