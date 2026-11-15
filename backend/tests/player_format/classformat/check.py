"""Whole-document checks: JSON Schema plus the semantic rules, strict or lax.

Producers use mode "strict" (the exact version they write, unknown fields refused). Readers use
"reader" (any minor of a known major, unknown fields ignored). The reader schema is derived from
the strict one, so there is one source. Pure given the schema dicts; the caller loads the files.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Literal

from jsonschema import Draft202012Validator

from .hashing import content_hash
from .semantic import check_semantics

Mode = Literal["strict", "reader"]
Violation = tuple[str, str]

KNOWN_MAJOR = 1
_VERSION = re.compile(r"^(\d+)\.(\d+)$")


def reader_schema(strict: dict[str, Any]) -> dict[str, Any]:
    """Derive the lax schema: unknown fields allowed, any minor of the known major."""
    lax = copy.deepcopy(strict)

    def relax(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("additionalProperties") is False:
                del node["additionalProperties"]
            for value in node.values():
                relax(value)
        elif isinstance(node, list):
            for value in node:
                relax(value)

    relax(lax)
    if "format_version" in lax.get("properties", {}):
        lax["properties"]["format_version"] = {"type": "string", "pattern": r"^1\.\d+$"}
    return lax


def _pointer(path: Any) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _schema_violations(schema: dict[str, Any], instance: Any, prefix: str) -> list[Violation]:
    out: list[Violation] = []

    def walk(error: Any) -> None:
        out.append(("schema", prefix + _pointer(error.absolute_path)))
        for sub in error.context or []:
            walk(sub)

    for err in Draft202012Validator(schema).iter_errors(instance):
        walk(err)
    return sorted(set(out))


def _version_problem(version: Any) -> Violation | None:
    match = _VERSION.match(version) if isinstance(version, str) else None
    if match is None:
        return ("schema", "/format_version")
    if int(match.group(1)) != KNOWN_MAJOR:
        return ("unsupported_major", "/format_version")
    return None


def check_document(
    class_schema: dict[str, Any], doc: Any, mode: Mode = "strict", prefix: str = ""
) -> list[Violation]:
    """All violations of a class document; empty means valid."""
    schema = class_schema
    if mode == "reader":
        version = doc.get("format_version") if isinstance(doc, dict) else None
        problem = _version_problem(version)
        if problem is not None:
            return [(problem[0], prefix + problem[1])]
        schema = reader_schema(class_schema)
    found = _schema_violations(schema, doc, prefix)
    return found if found else [(c, prefix + p) for c, p in check_semantics(doc)]


def _envelope_schema(publication_schema: dict[str, Any], mode: Mode) -> dict[str, Any]:
    env = copy.deepcopy(publication_schema)
    env["properties"]["document"] = {"type": "object"}
    return reader_schema(env) if mode == "reader" else env


def check_publication(
    class_schema: dict[str, Any],
    publication_schema: dict[str, Any],
    pub: Any,
    mode: Mode = "strict",
) -> list[Violation]:
    """All violations of a publication; includes the document and the content hash."""
    found = _schema_violations(_envelope_schema(publication_schema, mode), pub, "")
    if found:
        return found
    if pub.get("removed") is True:
        return []
    found = check_document(class_schema, pub["document"], mode, "/document")
    if found:
        return found
    if content_hash(pub["document"]) != pub["content_hash"]:
        return [("hash_mismatch", "/content_hash")]
    return []
