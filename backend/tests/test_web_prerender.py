"""The server puts the prerendered public pages in the response (ADR 0030 section 4, #892).

``spatools`` writes a fake ``prerender.json`` for every listed path and language. The build's real
output is checked by the frontend tests and the e2e specs. No database is reached.
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from classbeat import locales, spa, spa_content
from classbeat.config import Settings
from spatools import INDEX, fake_content, fake_manifest, fake_text, write_prerender
from test_web import make_app, make_client
from test_web_language import BASE, HOSTILE_PATHS, OFFERED, PUBLIC, canonical

REPO = Path(__file__).resolve().parents[2]
NOINDEX = '<meta name="robots" content="noindex">'


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    write_prerender(tmp_path)
    return root


def count(html: str, pattern: str) -> int:
    return len(re.findall(pattern, html))


# --- what an anonymous raw GET holds --------------------------------------------------------------


async def test_a_published_page_holds_its_languages_heading_text_title_and_description(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for code in OFFERED:
            for page in PUBLIC:
                html = (await client.get(f"/{code}{page}")).text
                text = fake_text(code, page)
                assert f'<div id="root">{text.body}</div>' in html, (code, page)
                assert html.count("<title") == 1, (code, page)
                assert html.count(f"<title>{text.title}</title>") == 1, (code, page)
                assert count(html, r'<meta name="description"') == 1, (code, page)
                assert f'content="{text.description}"' in html, (code, page)
                assert NOINDEX not in html, (code, page)
                assert count(html, "<h1") == 1, (code, page)


async def test_the_owner_given_urls_keep_a_literal_pin(
    unreachable_database_url: str, static_dir: Path
) -> None:
    """``/en/privacy`` and ``/en/terms`` were given to Google: they stay published, canonical to
    themselves and in the sitemap, whatever else the list holds."""
    assert {"", "/privacy", "/terms"} <= set(spa_content.PUBLISHED)
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        sitemap = (await client.get("/sitemap.xml")).text
        for url in (f"{BASE}/en/privacy", f"{BASE}/en/terms"):
            assert f"<loc>{url}</loc>" in sitemap
            path = url.removeprefix(BASE)
            page = (await client.get(path)).text
            assert canonical(page) == [url]
            assert NOINDEX not in page
            assert fake_text("en", path.removeprefix("/en")).body in page


async def test_app_pages_keep_an_empty_root_the_default_head_and_noindex(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/", "/sv/classes", "/sv/privacy/extra", "/en/signin", "/fr", "/en/admin"):
            html = (await client.get(path)).text
            assert '<div id="root"></div>' in html, path
            assert html.count(NOINDEX) == 1, path
            assert "<title>classbeat</title>" in html, path
            assert "Heading" not in html, path


async def test_a_draft_page_has_the_body_and_title_but_is_noindex_and_unlisted(
    unreachable_database_url: str, static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PP8 published every path, so the shipped list has no draft: move /terms to draft for this test
    drafted = "/terms"
    assert drafted in PUBLIC
    monkeypatch.setattr(spa_content, "PUBLISHED", tuple(p for p in PUBLIC if p != drafted))
    monkeypatch.setattr(spa_content, "DRAFT", (drafted,))
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        sitemap = (await client.get("/sitemap.xml")).text
        for page in (drafted,):
            assert page not in sitemap
            for code in OFFERED:
                html = (await client.get(f"/{code}{page}")).text
                text = fake_text(code, page)
                assert f'<div id="root">{text.body}</div>' in html
                assert html.count(f"<title>{text.title}</title>") == 1
                assert html.count(NOINDEX) == 1
                assert 'rel="canonical"' not in html
                assert "hreflang" not in html
                assert 'property="og:' not in html


async def test_a_language_that_is_not_offered_gets_no_prerendered_text(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(
        unreachable_database_url, static_dir=static_dir, locales="en,sv"
    ) as client:
        html = (await client.get("/da/privacy")).text
        assert '<div id="root"></div>' in html
        assert html.count(NOINDEX) == 1
        assert "Heading" not in html


# --- nothing from the request reaches the response ------------------------------------------------


async def test_hostile_hosts_and_paths_give_byte_identical_output(
    unreachable_database_url: str, static_dir: Path
) -> None:
    hosts = ("evil.example", "evil.example:8443", "127.0.0.1:8000.evil.example")
    paths = ("/sv", "/sv/privacy", "/en/terms", "/sv/help", "/sv/classes", "/", *HOSTILE_PATHS)
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in paths:
            plain = await client.get(path)
            for host in hosts:
                hostile = await client.get(path, headers={"Host": host})
                assert hostile.status_code == plain.status_code, (host, path)
                assert hostile.content == plain.content, (host, path)
            forwarded = await client.get(
                path, headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "gopher"}
            )
            assert forwarded.content == plain.content, path


def test_a_trailing_slash_selects_the_same_prerendered_page() -> None:
    pages = spa.build_pages(INDEX, Settings(), fake_content())
    assert pages.select("sv/privacy/") is pages.public[("sv", "/privacy")]
    assert pages.select("sv/help/") is pages.public[("sv", "/help")]


async def test_a_query_string_selects_the_same_prerendered_page(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/sv", "/sv/privacy", "/da/help", "/sv/classes"):
            plain = await client.get(path)
            queried = await client.get(f"{path}?x=<b>1</b>&lang=da")
            assert queried.content == plain.content, path
            assert "<b>1</b>" not in queried.text, path


async def test_injection_adds_no_script_and_leaves_the_csp_alone(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        plain_csp = (await client.get("/api/health")).headers["content-security-policy"]
        for path in ("/sv", "/sv/privacy", "/en/terms", "/da/help", "/sv/classes"):
            response = await client.get(path)
            assert response.text.count("<script") == INDEX.count("<script"), path
            assert " style=" not in response.text, path
            assert response.headers["content-security-policy"] == plain_csp, path


# --- the server refuses a build it cannot serve correctly -----------------------------------------


def test_web_refuses_to_start_when_index_html_exists_and_the_manifest_does_not(
    unreachable_database_url: str, tmp_path: Path
) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"prerender\.json is missing"):
        make_app(unreachable_database_url, static_dir=root)


def test_a_page_missing_from_the_manifest_refuses_the_start(
    unreachable_database_url: str, tmp_path: Path
) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    manifest = fake_manifest()
    del manifest["sv"]["/privacy"]
    (tmp_path / "prerender.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"sv/privacy"):
        make_app(unreachable_database_url, static_dir=root)


def test_a_disabled_language_may_be_missing_from_the_manifest() -> None:
    manifest = fake_manifest()
    del manifest["da"]
    offered = tuple(c for c in locales.SUPPORTED_LOCALES if c != "da")
    parsed = spa_content.parse_prerender(json.dumps(manifest), offered)
    assert {lang for lang, _ in parsed} == set(offered)


@pytest.mark.parametrize(
    "bad",
    [
        {"title": "T", "description": "D", "body": ""},
        {"title": "", "description": "D", "body": "<h1>x</h1>"},
        {"title": "T", "description": "D", "body": "<h1>x</h1><script>alert(1)</script>"},
        {"title": "T", "description": "D", "body": "<H1>x</H1><SCRIPT src=x>"},
        {"title": 1, "description": "D", "body": "<h1>x</h1>"},
        "not an object",
    ],
)
def test_a_malformed_manifest_entry_is_refused(bad: object) -> None:
    manifest = fake_manifest()
    manifest["en"][""] = bad  # pyright: ignore[reportArgumentType]
    with pytest.raises(RuntimeError, match=r"prerender\.json"):
        spa_content.parse_prerender(json.dumps(manifest), ("en",))


@pytest.mark.parametrize(
    "index",
    [
        INDEX.replace("<title>classbeat</title>", ""),
        INDEX.replace("<title>classbeat</title>", "<title>a</title><title>b</title>"),
        INDEX.replace(
            '<meta name="description" content="classbeat helps indoor-cycling instructors." />', ""
        ),
        INDEX.replace('<div id="root"></div>', '<div id="root"></div><div id="root"></div>'),
        INDEX.replace('<div id="root"></div>', '<div id="root"><p>x</p></div>'),
    ],
)
def test_an_index_that_is_not_exactly_one_title_description_and_empty_root_is_refused(
    index: str,
) -> None:
    with pytest.raises(RuntimeError, match="exactly one"):
        spa.build_pages(index, Settings(), fake_content())


def test_the_shipped_index_html_is_one_title_one_description_and_an_empty_root() -> None:
    index = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    page = spa.build_pages(index, Settings(), fake_content()).public[("sv", "")]
    assert page.count("<title>") == 1
    assert count(page, r'<meta name="description"') == 1
    assert fake_text("sv", "").body in page


def test_the_path_lists_are_the_files_the_frontend_reads() -> None:
    data = json.loads(
        (REPO / "frontend" / "src" / "publicSite" / "publicPaths.json").read_text(encoding="utf-8")
    )
    assert (
        tuple(data["published"]),
        tuple(data["draft"]),
    ) == (spa_content.PUBLISHED, spa_content.DRAFT)
    assert replace(fake_content(), draft=()).listed == spa_content.PUBLISHED
