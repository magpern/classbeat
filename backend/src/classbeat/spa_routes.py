"""Serving the built single-page app, ``/sitemap.xml`` and ``/robots.txt`` (ADR 0027)."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse

from classbeat import spa
from classbeat.config import Settings

# HEAD answers like GET (FastAPI adds no HEAD to a GET route); the server drops the body.
_READ = ["GET", "HEAD"]

# The picture formats of ``public/img`` (issue 854), named here and not left to the platform's
# ``mimetypes`` table, which an operating system or Python build may lack or change: a file answered
# as text/plain is not drawn (the CSP and nosniff forbid guessing).
IMAGE_MEDIA_TYPES = {".avif": "image/avif", ".webp": "image/webp", ".jpg": "image/jpeg"}
# The files keep their names when a picture is replaced, so they are cached for a day, not for good.
IMAGE_HEADERS = {"Cache-Control": "public, max-age=86400"}

EMAIL_ASSET_HEADERS = {
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Cache-Control": "public, max-age=604800",
}


SEO_HEADERS = {"Cache-Control": "public, max-age=3600"}


def add_single_page_app(app: FastAPI, static_dir: Path, settings: Settings) -> None:
    """Serve ``static_dir``; any other GET or HEAD outside ``/api`` gets ``index.html``.

    ``index.html`` is read once; a language-prefixed or unprefixed page is answered with the
    variant ``classbeat.spa`` built from it (ADR 0027 section 6), a two-letter first segment
    outside the manifest with a 404 (section 5). ``/sitemap.xml`` and ``/robots.txt`` come first.
    """
    root = static_dir.resolve()
    try:
        index_html: str | None = (root / "index.html").read_text(encoding="utf-8")
    except OSError:
        index_html = None
    pages = None if index_html is None else spa.build_pages(index_html, settings)
    sitemap_xml, robots_txt = spa.sitemap(settings), spa.robots(settings)

    @app.api_route("/sitemap.xml", methods=_READ, include_in_schema=False)
    async def sitemap_route() -> Response:
        return Response(sitemap_xml, media_type="application/xml", headers=SEO_HEADERS)

    @app.api_route("/robots.txt", methods=_READ, include_in_schema=False)
    async def robots_route() -> Response:
        return Response(robots_txt, media_type="text/plain", headers=SEO_HEADERS)

    @app.api_route("/{path:path}", methods=_READ, include_in_schema=False)
    async def single_page_app(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            candidate = (root / path).resolve()
            if (
                path != "index.html"  # the page itself goes through the head injection below
                and candidate.is_relative_to(root)
                and candidate.is_file()
            ):
                if path.startswith("email/"):
                    # The logo in mail is loaded cross-origin by mail clients (ADR 0022
                    # section 3); only /email/* is opened up, and it may be cached for a week.
                    return FileResponse(candidate, headers=EMAIL_ASSET_HEADERS)
                if path.startswith("img/"):
                    return FileResponse(
                        candidate,
                        media_type=IMAGE_MEDIA_TYPES.get(candidate.suffix.lower()),
                        headers=IMAGE_HEADERS,
                    )
                return FileResponse(candidate)
        except (OSError, ValueError):  # for example an embedded NUL in the path
            pass
        if pages is None:
            raise HTTPException(status_code=404)
        unknown = spa.is_unknown_language(path.partition("/")[0])
        return HTMLResponse(
            pages.select(path), status_code=404 if unknown else 200, headers=spa.NO_CACHE
        )
