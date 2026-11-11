"""The link-preview image on the public pages and the picture files' types (issue 854).

The image is a head tag set for the public pages only; canonical and hreflang stay as ADR 0027
has them (pinned in ``test_web_language``). No database is reached.
"""

import re
import struct
from pathlib import Path

import pytest

from classbeat import locales, spa, spa_preview
from classbeat.config import Settings
from spatools import INDEX, fake_content, write_prerender
from test_web import make_client
from test_web_language import BASE, PUBLIC

REPO = Path(__file__).resolve().parents[2]
OG_FILE = REPO / "frontend" / "public" / "img" / "og-share.jpg"
OFFERED = locales.offered_locales(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]


def og_tags(html: str) -> list[str]:
    return re.findall(r'<meta (?:property|name)="((?:og|twitter):[^"]+)" content="([^"]*)">', html)


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Width and height from the first start-of-frame marker."""
    pos = 2
    while pos + 9 < len(data):
        assert data[pos] == 0xFF
        marker, length = data[pos + 1], struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return width, height
        pos += 2 + length
    raise AssertionError("no start-of-frame marker")


def test_a_public_page_names_the_absolute_image_its_size_and_the_large_card() -> None:
    pages = spa.build_pages(INDEX, Settings(), fake_content())
    for code in OFFERED:
        for page in PUBLIC:
            tags = [
                t for t in og_tags(pages.public[(code, page)]) if "image" in t[0] or "card" in t[0]
            ]
            assert tags == [
                ("og:image", f"{BASE}/img/og-share.jpg"),
                ("og:image:width", "1200"),
                ("og:image:height", "630"),
                ("twitter:card", "summary_large_image"),
            ]


def test_the_image_url_comes_from_public_base_url() -> None:
    pages = spa.build_pages(
        INDEX, Settings(public_base_url="https://classbeat.example.test/"), fake_content()
    )
    html = pages.public[("sv", "")]
    assert ("og:image", "https://classbeat.example.test/img/og-share.jpg") in og_tags(html)


def test_pages_that_are_not_public_carry_no_preview_tags() -> None:
    pages = spa.build_pages(INDEX, Settings(), fake_content())
    for html in (pages.plain_noindex, *pages.by_lang_noindex.values()):
        assert og_tags(html) == []


def test_the_shipped_image_has_the_size_the_tags_state() -> None:
    assert jpeg_size(OG_FILE.read_bytes()) == spa_preview.OG_IMAGE_SIZE
    assert OG_FILE.stat().st_size < 150_000  # a crawler may give up on a big preview


def test_the_real_index_html_has_no_preview_tags_of_its_own() -> None:
    # the server adds them per page; a second set in the file would be a duplicate
    index = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    assert og_tags(index) == []


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("a-480.avif", "image/avif"),
        ("a-480.webp", "image/webp"),
        ("a-480.jpg", "image/jpeg"),
    ],
)
async def test_the_picture_files_are_served_with_their_own_type(
    unreachable_database_url: str, tmp_path: Path, name: str, media_type: str
) -> None:
    root = tmp_path / "dist"
    (root / "img").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    write_prerender(tmp_path)
    (root / "img" / name).write_bytes(b"\x00")
    async with make_client(unreachable_database_url, static_dir=root) as client:
        response = await client.get(f"/img/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"] == media_type


async def test_the_picture_types_do_not_depend_on_the_platforms_table(
    unreachable_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Python build or operating system whose mimetypes table knows none of the formats.
    # FileResponse binds ``guess_type`` at import, so that name is the one it uses.
    monkeypatch.setattr("starlette.responses.guess_type", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr("mimetypes.guess_type", lambda *args, **kwargs: (None, None))
    root = tmp_path / "dist"
    (root / "img").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    write_prerender(tmp_path)
    for name in ("a-480.avif", "a-480.webp", "a-480.jpg"):
        (root / "img" / name).write_bytes(b"\x00")
    async with make_client(unreachable_database_url, static_dir=root) as client:
        for name, media_type in (
            ("a-480.avif", "image/avif"),
            ("a-480.webp", "image/webp"),
            ("a-480.jpg", "image/jpeg"),
        ):
            response = await client.get(f"/img/{name}")
            assert response.status_code == 200
            assert response.headers["content-type"] == media_type


async def test_the_pictures_are_cached_for_a_day_and_nothing_else_in_the_app_is(
    unreachable_database_url: str, tmp_path: Path
) -> None:
    root = tmp_path / "dist"
    (root / "img").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    write_prerender(tmp_path)
    (root / "img" / "a-480.webp").write_bytes(b"\x00")
    (root / "app.js").write_text("1", encoding="utf-8")
    async with make_client(unreachable_database_url, static_dir=root) as client:
        picture = await client.get("/img/a-480.webp")
        assert picture.headers["cache-control"] == "public, max-age=86400"
        assert picture.headers["content-type"] == "image/webp"
        assert "cache-control" not in (await client.get("/app.js")).headers
