"""Shared fixtures for the single-page-app tests: an ``index.html`` shaped like the built one and a
fake ``prerender.json`` for every listed public path in every supported language (ADR 0030)."""

import json
from pathlib import Path

from classbeat import locales, spa_content
from classbeat.spa_content import PageText, PublicContent

INDEX = (
    '<!doctype html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n'
    '    <meta name="description" content="classbeat helps indoor-cycling instructors." />\n'
    '    <title>classbeat</title>\n    <script src="/locale-init.js"></script>\n  </head>\n'
    '  <body><div id="root"></div><script type="module" src="/assets/app.js"></script></body>\n'
    "</html>\n"
)


def fake_text(lang: str, path: str) -> PageText:
    where = f"{lang}{path}"
    return PageText(
        title=f"Title {where} | classbeat",
        description=f"Description of {where}.",
        body=f'<main><h1>Heading {where}</h1><p class="lead">Text {where}</p></main>',
    )


def fake_manifest() -> dict[str, dict[str, dict[str, str]]]:
    return {
        lang: {
            path: {
                "title": fake_text(lang, path).title,
                "description": fake_text(lang, path).description,
                "body": fake_text(lang, path).body,
            }
            for path in (*spa_content.PUBLISHED, *spa_content.DRAFT)
        }
        for lang in locales.SUPPORTED_LOCALES
    }


def fake_content() -> PublicContent:
    return PublicContent(
        spa_content.PUBLISHED,
        spa_content.DRAFT,
        {
            (lang, path): fake_text(lang, path)
            for lang in locales.SUPPORTED_LOCALES
            for path in (*spa_content.PUBLISHED, *spa_content.DRAFT)
        },
    )


def write_prerender(directory: Path) -> None:
    """``prerender.json`` beside the ``dist`` folder, where the server looks for it."""
    (directory / "prerender.json").write_text(json.dumps(fake_manifest()), encoding="utf-8")
