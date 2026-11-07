"""Language-prefixed URLs, server half (ADR 0027 sections 5 and 6, #719). No database is reached."""

import re
from pathlib import Path

import pytest

from classbeat import locales, spa
from classbeat.config import Settings
from test_web import make_client

REPO = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8000"  # Settings' default public_base_url
INDEX = (
    '<!doctype html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n'
    '    <title>classbeat</title>\n    <script src="/locale-init.js"></script>\n  </head>\n'
    '  <body><div id="root"></div><script type="module" src="/assets/app.js"></script></body>\n'
    "</html>\n"
)
PUBLIC = ("", "/privacy", "/terms")
# Every language the default settings offer (manifest and default `locales`): the head tags and
# the sitemap list these, so a new language is checked without editing a list here.
OFFERED = locales.offered_locales(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('app');", encoding="utf-8")
    return root


def lang_of(html: str) -> str:
    match = re.search(r'<html lang="([^"]*)"', html)
    assert match
    return match.group(1)


def alternates(html: str) -> dict[str, str]:
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', html))


def canonical(html: str) -> list[str]:
    return re.findall(r'<link rel="canonical" href="([^"]+)">', html)


def all_pages(settings: Settings) -> set[str]:
    pages = spa.build_pages(INDEX, settings)
    return {pages.plain_noindex, *pages.by_lang_noindex.values(), *pages.public.values()}


# --- the guard: a two-letter first segment outside the manifest -------------------------------


async def test_an_unknown_two_letter_language_is_a_404_with_the_app_shell(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/fr", "/fr/", "/fr/privacy", "/zz/classes/new", "/xx/a/b/c", "/%66r/privacy"):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-type"].startswith("text/html"), path
            assert '<div id="root">' in response.text, path
            assert response.headers["cache-control"] == "no-cache", path
            assert 'content="noindex"' in response.text, path


async def test_everything_else_is_unchanged_by_the_guard(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in (
            "/",
            "/classes",
            "/admin",
            "/admin/users",
            "/FR/privacy",  # not two lowercase letters
            "/fra/privacy",
            "/f/privacy",
            "/f1/privacy",
            "/en/privacy",
            "/sv/classes/new",
            "/en/admin",
            "/en/nothing/here",
            "/privacy",
        ):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert '<div id="root">' in response.text, path
        assert (await client.get("/assets/app.js")).text == "console.log('app');"


def test_the_server_rule_and_the_manifest_agree() -> None:
    for code in locales.SUPPORTED_LOCALES:
        assert not spa.is_unknown_language(code)
    for other in ("fr", "de", "zz", "aa"):
        if other not in locales.SUPPORTED_LOCALES:
            assert spa.is_unknown_language(other)
    for not_a_code in ("", "e", "EN", "en-", "eng", "e1", "é", "api", "oauth"):
        assert not spa.is_unknown_language(not_a_code)


def test_no_top_level_name_in_frontend_public_is_a_two_letter_code() -> None:
    """A file or folder named like a language would be shadowed (or shadow a prefix): ADR 0027
    section 5. Vite copies ``public/`` to the root of the build: these are its top-level names."""
    names = [p.name for p in (REPO / "frontend" / "public").iterdir()]
    assert names  # the folder is where it is expected
    for name in names:
        assert not re.fullmatch(r"[a-z]{2}", name.split(".")[0].lower()), name
        assert not re.fullmatch(r"[a-z]{2}", name), name


def test_the_real_index_html_can_take_the_injection() -> None:
    index = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    assert len(re.findall(r'<html lang="[^"]*"', index)) == 1
    assert index.count("</head>") == 1
    page = spa.build_pages(index, Settings()).public[("sv", "/privacy")]
    assert lang_of(page) == "sv"
    assert canonical(page) == [f"{BASE}/sv/privacy"]


# --- head injection -----------------------------------------------------------------------------


async def test_a_public_page_gets_lang_canonical_and_every_enabled_alternate(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for page in PUBLIC:
            for code in OFFERED:
                response = await client.get(f"/{code}{page}")
                html = response.text
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-cache"
                assert lang_of(html) == code
                assert canonical(html) == [f"{BASE}/{code}{page}"]
                assert alternates(html) == {
                    **{c: f"{BASE}/{c}{page}" for c in OFFERED},
                    "x-default": f"{BASE}/en{page}",
                }
                assert "noindex" not in html


async def test_the_canonical_form_has_no_trailing_slash(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/sv/", "/sv/privacy/"):
            html = (await client.get(path)).text
            assert canonical(html) == [f"{BASE}{path.rstrip('/')}"], path
            assert not any(u.endswith("/") for u in alternates(html).values()), path


async def test_only_one_trailing_slash_is_the_same_page(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/sv/privacy//", "/sv//"):
            html = (await client.get(path)).text
            assert canonical(html) == [], path
            assert "noindex" in html, path
            assert lang_of(html) == "sv", path


async def test_only_enabled_languages_are_alternates_and_a_disabled_one_is_noindex(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(
        unreachable_database_url, static_dir=static_dir, locales="en,sv"
    ) as client:
        html = (await client.get("/sv/terms")).text
        assert set(alternates(html)) == {"en", "sv", "x-default"}
        assert "/da" not in html
        disabled = await client.get("/da/terms")
        assert disabled.status_code == 200  # the client shows the best offered language
        assert 'content="noindex"' in disabled.text
        assert not canonical(disabled.text)
        assert lang_of(disabled.text) == "en"


async def test_other_pages_are_noindex_and_the_unprefixed_language_is_untouched(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path, lang in (
            ("/sv/classes/new", "sv"),
            ("/da/signin", "da"),
            ("/en/admin", "en"),
            ("/sv/privacy/extra", "sv"),
            ("/sv/help", "sv"),
            ("/", "en"),
            ("/classes", "en"),
            ("/privacy", "en"),
            ("/admin/users", "en"),
            ("/index.html", "en"),
            ("/assets/missing.js", "en"),
        ):
            response = await client.get(path)
            html = response.text
            assert response.status_code == 200, path
            assert html.count('<meta name="robots" content="noindex">') == 1, path
            assert lang_of(html) == lang, path
            assert not canonical(html), path
            assert not alternates(html), path
            assert response.headers["cache-control"] == "no-cache", path


async def test_injection_adds_no_script_and_leaves_the_csp_and_assets_alone(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        asset = await client.get("/assets/app.js")
        assert "no-cache" not in asset.headers.get("cache-control", "")
        plain_csp = (await client.get("/api/health")).headers["content-security-policy"]
        for path in ("/sv", "/sv/privacy", "/sv/classes", "/classes", "/fr"):
            response = await client.get(path)
            assert response.text.count("<script") == INDEX.count("<script"), path
            assert "on" + "error=" not in response.text
            assert response.headers["content-security-policy"] == plain_csp, path


# --- nothing from the request reaches the page ----------------------------------------------------


HOSTILE_PATHS = (
    "//evil.example",
    "//evil.example/privacy",
    "/sv//evil.example",
    "/sv/privacy//evil.example",
    "/sv/privacy/../../evil",
    "/sv/privacy%2F..%2Fevil",
    "/sv/%3Cscript%3Ealert(1)%3C/script%3E",
    "/sv/privacy?next=https://evil.example&lang=%22%3E%3Cscript%3E",
    "/%73v/privacy",  # an encoded letter: decoded by the server before routing
    "/sv/privacy%00",
    "/sv/privacy%0d%0aX-Evil:1",
    "/sv\\evil.example",
    "/sv/privacy#evil",
)


async def test_the_page_cannot_be_influenced_by_the_host_header_or_the_path(
    unreachable_database_url: str, static_dir: Path
) -> None:
    known = all_pages(Settings())
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for host in ("evil.example", "evil.example:8443", "127.0.0.1:8000.evil.example"):
            for path in ("/sv/privacy", "/en", "/sv/classes", "/", *HOSTILE_PATHS):
                response = await client.get(path, headers={"Host": host})
                assert "evil" not in response.text.lower(), (host, path)
                assert "<script>" not in response.text, (host, path)
                if response.status_code == 200:
                    # always one of the pages built at start: a path only selects, never builds
                    assert response.text in known, (host, path)
                assert "evil" not in str(response.headers).lower(), (host, path)
        # X-Forwarded-* are not read either
        response = await client.get(
            "/sv/privacy",
            headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "gopher"},
        )
        assert canonical(response.text) == [f"{BASE}/sv/privacy"]


async def test_the_urls_come_from_public_base_url_only(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(
        unreachable_database_url,
        static_dir=static_dir,
        public_base_url="https://classbeat.example.test",
    ) as client:
        html = (await client.get("/da/privacy", headers={"Host": "evil.example"})).text
        assert canonical(html) == ["https://classbeat.example.test/da/privacy"]
        assert all(
            u.startswith("https://classbeat.example.test/") for u in alternates(html).values()
        )
        sitemap = (await client.get("/sitemap.xml", headers={"Host": "evil.example"})).text
        assert "evil" not in sitemap
        assert "https://classbeat.example.test/da/privacy" in sitemap
        robots = (await client.get("/robots.txt", headers={"Host": "evil.example"})).text
        assert "evil" not in robots
        assert "Sitemap: https://classbeat.example.test/sitemap.xml" in robots


def test_every_built_string_is_escaped() -> None:
    # model_construct skips the origin validator: the point is that the builders escape anyway
    hostile = Settings.model_construct(public_base_url='https://x.test/"><script>alert(1)</script>')
    pages = spa.build_pages(INDEX, hostile)
    for html in {*pages.public.values()}:
        assert "<script>alert" not in html
        assert "&quot;&gt;&lt;script&gt;" in html
    sitemap = spa.sitemap(hostile)
    assert "<script>" not in sitemap
    assert "&quot;&gt;&lt;script&gt;" in sitemap


def test_the_page_is_selected_by_comparison_with_the_known_lists_only() -> None:
    pages = spa.build_pages(INDEX, Settings())
    assert pages.select("sv/privacy") is pages.public[("sv", "/privacy")]
    assert pages.select("sv/privacy/") is pages.public[("sv", "/privacy")]
    assert pages.select("sv") is pages.public[("sv", "")]
    assert pages.select("sv/") is pages.public[("sv", "")]
    assert pages.select("sv/privacy/x") is pages.by_lang_noindex["sv"]
    assert pages.select("sv//privacy") is pages.by_lang_noindex["sv"]
    assert pages.select("") is pages.plain_noindex
    assert pages.select("privacy") is pages.plain_noindex
    assert pages.select("/evil") is pages.plain_noindex


# --- sitemap.xml and robots.txt -------------------------------------------------------------------


async def test_the_sitemap_lists_the_public_pages_in_every_enabled_language(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        response = await client.get("/sitemap.xml")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        xml = response.text
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
        locs = re.findall(r"<loc>([^<]+)</loc>", xml)
        assert sorted(locs) == sorted(f"{BASE}/{c}{p}" for c in OFFERED for p in PUBLIC)
        urls = len(OFFERED) * len(PUBLIC)
        assert xml.count("<url>") == urls
        # every url carries an alternate for each language and x-default
        assert xml.count('rel="alternate"') == urls * (len(OFFERED) + 1)
        assert f'hreflang="x-default" href="{BASE}/en/privacy"/>' in xml
        assert f'hreflang="da" href="{BASE}/da"/>' in xml
        for private in ("/admin", "/classes", "/signin"):
            assert private not in xml


async def test_the_sitemap_follows_the_enabled_languages(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir, locales="en") as client:
        xml = (await client.get("/sitemap.xml")).text
        assert xml.count("<url>") == 3
        assert "/sv" not in xml
        assert "/da" not in xml


async def test_robots_allows_everything_and_names_the_sitemap(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        response = await client.get("/robots.txt")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text == f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n"
        assert "Disallow" not in response.text


async def test_sitemap_and_robots_are_not_answered_by_the_app_shell(
    unreachable_database_url: str, static_dir: Path
) -> None:
    (static_dir / "sitemap.xml").write_text("<stale/>", encoding="utf-8")
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        assert "<stale/>" not in (await client.get("/sitemap.xml")).text
        assert '<div id="root">' not in (await client.get("/robots.txt")).text
        assert '<div id="root">' not in (await client.get("/sitemap.xml")).text


# --- what must not change -------------------------------------------------------------------------


async def test_api_and_oauth_paths_are_not_touched(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path in ("/api/nope", "/api/fr/privacy", "/api"):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-type"] == "application/json", path
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        # a provider callback is still the route, not the shell and not a language guard
        callback = await client.get("/oauth/nonesuch/callback")
        assert '<div id="root">' not in callback.text
        assert callback.status_code != 200 or "classbeat" not in callback.text


async def test_without_static_dir_the_new_routes_are_absent_too(
    unreachable_database_url: str,
) -> None:
    async with make_client(unreachable_database_url) as client:
        assert (await client.get("/en/privacy")).status_code == 404
        assert (await client.get("/sitemap.xml")).status_code == 404


async def test_head_answers_like_get_on_the_seo_files_and_pages(
    unreachable_database_url: str, static_dir: Path
) -> None:
    async with make_client(unreachable_database_url, static_dir=static_dir) as client:
        for path, status in (
            ("/sitemap.xml", 200),
            ("/robots.txt", 200),
            ("/da/privacy", 200),
            ("/fr/privacy", 404),
        ):
            got, head = await client.get(path), await client.head(path)
            assert head.status_code == got.status_code == status, path
            assert head.content == b"", path
            assert head.headers["content-type"] == got.headers["content-type"], path
            assert head.headers["cache-control"] == got.headers["cache-control"], path
