"""What the single-page app's public pages are made of (ADR 0030 sections 2 and 4).

Two checked-in or build-time files, read once at start:

* ``publicPaths.json`` (``frontend/src/publicSite/``; the image carries a copy at
  ``/app/publicPaths.json``): the ``published`` pages, which are indexable in every language, and
  the ``draft`` pages, which are prerendered but ``noindex``. Paths are language-neutral; ``""`` is
  the landing page.
* ``prerender.json`` (the frontend build's output, ``/app/prerender.json`` in the image): language,
  then path, then the page's ``title``, ``description`` and ``body`` (the markup of ``#root``).

Nothing here reads a request. A page's text comes from the build, and the request path only selects
one of them (``spa.SpaPages.select``). If ``index.html`` is served and the manifest is missing or
malformed, ``load_content`` raises, so web refuses to start instead of silently serving every
public page empty.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PATH = re.compile(r"(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*")  # "" or /a or /a/b-c: no trailing slash


@dataclass(frozen=True, slots=True)
class PageText:
    """One prerendered page in one language."""

    title: str
    description: str
    body: str


@dataclass(frozen=True, slots=True)
class PublicContent:
    published: tuple[str, ...]
    draft: tuple[str, ...]
    text: Mapping[tuple[str, str], PageText]  # (language, path) -> page

    @property
    def listed(self) -> tuple[str, ...]:
        return (*self.published, *self.draft)


def _paths_file() -> Path:
    here = Path(__file__).resolve()
    candidates = (
        here.parents[3] / "frontend" / "src" / "publicSite" / "publicPaths.json",  # the repository
        here.parents[2] / "publicPaths.json",  # the image (/app/publicPaths.json)
    )
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("the public page list publicPaths.json was not found")


def _path_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(p, str) and PATH.fullmatch(p) for p in value
    ):
        raise RuntimeError(f"publicPaths.json: {name} must be a list of neutral paths")
    return tuple(value)


def parse_paths(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("publicPaths.json must be an object")
    published = _path_list(data.get("published"), "published")
    draft = _path_list(data.get("draft"), "draft")
    if len({*published, *draft}) != len(published) + len(draft):
        raise RuntimeError("publicPaths.json: a path is listed twice")
    return published, draft


PUBLISHED, DRAFT = parse_paths(_paths_file().read_text(encoding="utf-8"))


def _page_text(value: object, where: str) -> PageText:
    if not isinstance(value, dict):
        raise RuntimeError(f"prerender.json: {where} must be an object")
    fields = [value.get(name) for name in ("title", "description", "body")]
    if not all(isinstance(f, str) and f for f in fields):
        raise RuntimeError(f"prerender.json: {where} needs a title, a description and a body")
    title, description, body = (str(f) for f in fields)
    # The body is markup from our own build, put in the page as it is. It never holds a script: the
    # build's checks refuse one too, and this is the last stop before it is served.
    if "<script" in body.lower():
        raise RuntimeError(f"prerender.json: {where} holds a script")
    return PageText(title, description, body)


def parse_prerender(text: str, languages: tuple[str, ...]) -> dict[tuple[str, str], PageText]:
    """Every listed path in each of ``languages``, or an error naming what is missing."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("prerender.json must be an object")
    out: dict[tuple[str, str], PageText] = {}
    for lang in languages:
        pages = data.get(lang)
        if not isinstance(pages, dict):
            raise RuntimeError(f"prerender.json: no pages for the language {lang}")
        for path in (*PUBLISHED, *DRAFT):
            out[(lang, path)] = _page_text(pages.get(path), f"{lang}{path or '/'}")
    return out


def load_content(static_root: Path, languages: tuple[str, ...]) -> PublicContent:
    """The manifest next to the served build: ``/app/prerender.json`` beside ``/app/static``, or
    the frontend's own ``build/`` when ``static_root`` is ``frontend/dist``."""
    for candidate in (
        static_root.parent / "prerender.json",
        static_root.parent / "build" / "prerender.json",
    ):
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            return PublicContent(PUBLISHED, DRAFT, parse_prerender(text, languages))
    raise RuntimeError(
        "index.html is served but prerender.json is missing: the image is incomplete (ADR 0030)"
    )
