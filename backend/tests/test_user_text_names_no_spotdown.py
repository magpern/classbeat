"""No text the backend shows or sends a user names spotdown (owner, 2026-11-27, #1571).

"For the user classbeat is all they know. Spotdown is just a service for classbeat." The user sees
the backend's words in the account mail (``emails.locales``, every language), the pool consent
(``CONSENT_TEXT``) and the job and Spotify failure sentences (``jobs.sentences``,
``spotify.errors``); API errors are reason codes, never prose. Code, logs, docs and ADRs keep the
name. The frontend catalogues have their own guard (``noSpotdownInCatalogues.test.ts``).

The text classbeat writes into the user's own accounts is user text too (#1575): the Spotify
playlist description and name, the SwedeBeat playlist and mix names, and the notes about what stays
in the accounts. Those modules are named in ``ACCOUNT_TEXT_FILES`` (nothing in a file name says
they hold text), and a test fails if one is renamed or moved. The workout text saved to MOWL is
the class's own (the instructor's and the model's words), not a classbeat constant.

The list of what is read is found, not written out (#1573): every language ``emails.catalogue``
carries, and every module under ``classbeat`` whose file name says it holds user text (a
``sentence``, ``errors``, ``consent``, ``message`` or ``wording`` module, or any module of
``emails/locales``). A new locale or a new such module is covered without editing this file. Every
string literal in them is read, in a function body too, except docstrings. A module of user text
that is named otherwise is not found: name it so, or add its pattern to ``USER_TEXT_FILE``.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import classbeat
from classbeat.connections import spotify_consent_policy
from classbeat.emails.catalogue import LOCALES as MAIL
from classbeat.jobs import sentences

SRC = Path(classbeat.__file__).parent
USER_TEXT_FILE = re.compile(r"sentence|errors?\.py|consent|messages?\.py|wording")
ACCOUNT_TEXT_FILES = (
    "playlists/common.py",  # the Spotify playlist's description and the pool's name
    "songs/phase2.py",  # the final playlist's name fallback
    "studio/output_body_policy.py",  # the SwedeBeat playlist and mix names
    "classes/removal.py",  # what stays in the user's accounts after a removal
)


def user_text_files() -> list[Path]:
    found = {
        path
        for path in SRC.rglob("*.py")
        if USER_TEXT_FILE.search(path.name)
        or (path.parent.name == "locales" and path.parent.parent.name == "emails")
    }
    return sorted(found | {SRC / name for name in ACCOUNT_TEXT_FILES})


def literals(source: str, where: str) -> Iterator[tuple[str, str]]:
    """Every string literal in ``source`` that is not a docstring."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield f"{where}:{node.lineno}", node.value


def texts(value: object, where: str) -> Iterator[tuple[str, str]]:
    """Every string in ``value``: itself, or inside a mapping, sequence or set, at any depth."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from texts(inner, f"{where}[{key!r}]")
    elif isinstance(value, list | tuple | set | frozenset):
        for i, inner in enumerate(value):
            yield from texts(inner, f"{where}[{i}]")


def module_texts(module: ModuleType) -> Iterator[tuple[str, str]]:
    """The module's own constants (not its docstring, not what it imports by name)."""
    for name, value in vars(module).items():
        if name.startswith("__") or isinstance(value, ModuleType):
            continue
        yield from texts(value, f"{module.__name__}.{name}")


def naming_spotdown(found: Iterator[tuple[str, str]]) -> list[str]:
    return [where for where, text in found if "spotdown" in text.lower()]


@pytest.mark.parametrize("lang", sorted(MAIL))
def test_no_mail_string_names_spotdown(lang: str) -> None:
    catalogue = MAIL[lang]
    assert len(catalogue) > 50
    assert naming_spotdown(texts(catalogue, lang)) == []


def test_every_language_the_mail_carries_is_read() -> None:
    locale_files = {p.stem for p in (SRC / "emails" / "locales").glob("*.py")} - {
        "__init__",
        "untranslated",
    }
    assert locale_files <= set(MAIL)


def test_the_modules_that_write_text_into_user_accounts_exist() -> None:
    assert [name for name in ACCOUNT_TEXT_FILES if not (SRC / name).is_file()] == []


def test_the_user_text_modules_are_found() -> None:
    names = {path.relative_to(SRC).as_posix() for path in user_text_files()}
    assert {
        "jobs/sentences.py",
        "spotify/errors.py",
        "connections/spotify_consent_policy.py",
        "emails/locales/da.py",
        *ACCOUNT_TEXT_FILES,
    } <= names


@pytest.mark.parametrize("path", user_text_files(), ids=lambda p: p.relative_to(SRC).as_posix())
def test_no_user_text_module_names_spotdown(path: Path) -> None:
    found = list(literals(path.read_text(encoding="utf-8"), path.name))
    assert naming_spotdown(iter(found)) == []


def test_the_consent_text_and_the_song_sentences_are_read() -> None:
    read = dict(module_texts(spotify_consent_policy)) | dict(module_texts(sentences))
    assert read["classbeat.connections.spotify_consent_policy.CONSENT_TEXT"] == (
        spotify_consent_policy.CONSENT_TEXT
    )
    assert any(where.startswith("classbeat.jobs.sentences.SONG_SENTENCES[") for where in read)


def test_the_check_finds_the_name_in_any_case_and_at_any_depth() -> None:
    sample = {"a": "Analysed by Spotdown.", "b": ("x", {"c": "SPOTDOWN"}), "d": "classbeat"}
    assert naming_spotdown(texts(sample, "s")) == ["s['a']", "s['b'][1]['c']"]


def test_the_literal_scan_reads_function_bodies_and_skips_docstrings() -> None:
    source = (
        '"""Spotdown in a module docstring."""\n'
        "def f():\n"
        '    """Spotdown in a docstring."""\n'
        '    return "Analysed by Spotdown."\n'
    )
    assert [text for _, text in literals(source, "m")] == ["Analysed by Spotdown."]
