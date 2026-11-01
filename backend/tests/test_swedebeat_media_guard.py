"""Guard: the MOWL media host list of ADR 0016 §4 and the ban on Swedebeat's embedded AWS
credentials (AGENTS.md; threat model T109). Static checks over the repository's own files, no
network, no key.

- ``MOWL_MEDIA_HOSTS`` is a literal: ``frozenset()`` or ``frozenset({"exact.host", ...})`` of string
  constants, assigned once in ``classbeat.egress``; the media allowlist is built from it alone, and
  nothing else in ``classbeat`` names either (no setting, environment or file feeds the list).
- It is empty until the owner confirms the host; the production lists are what they were
  (``WORKER_HOSTS``, ``WEB_URLLIB_HOSTS``) and hold no media host, whatever it will be.
- No file of the repository imports or names an AWS SDK, signs with AWS, or holds an access key.
"""

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from classbeat import egress

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src" / "classbeat"
EGRESS_FILE = SRC / "egress.py"
THIS = Path(__file__).resolve()

# What the lists held when A1 was written (ADR 0016 §4: A1 changes neither).
WORKER_HOSTS_BEFORE = frozenset({"api.mowl.com", "api.spotify.com", "accounts.spotify.com"})
WEB_HOSTS_BEFORE = frozenset({"api.mowl.com", "api.spotify.com", "www.swedebeat.se"})


def egress_tree() -> ast.Module:
    return ast.parse(EGRESS_FILE.read_text(encoding="utf-8"))


def assignments(tree: ast.Module, name: str) -> list[ast.expr]:
    """The values assigned to module-level ``name`` (also annotated)."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            found.append(value)
    return found


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple | ast.List):
        return [n for element in target.elts for n in _target_names(element)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def rebinds(tree: ast.AST, name: str) -> list[str]:
    """Every way the code can bind or change module-level ``name`` other than reading it: plain,
    annotated and augmented (``|=``) assignment, a walrus, a loop, ``with`` or comprehension
    target, an import, a def or class of that name, ``del``, a ``global`` statement, and any use
    of ``globals()``, ``vars()``, ``locals()``, ``exec`` or ``eval`` (which can write it by name).
    The media list is written once, as a literal; this finds the rest."""
    found: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(name in _target_names(t) for t in targets):
            found.append("assign")
        elif isinstance(node, ast.AugAssign) and name in _target_names(node.target):
            found.append("augmented assignment")
        elif isinstance(node, ast.NamedExpr) and name in _target_names(node.target):
            found.append("walrus")
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension) and name in _target_names(
            node.target
        ):
            found.append("loop target")
        elif (
            isinstance(node, ast.withitem)
            and node.optional_vars is not None
            and name in _target_names(node.optional_vars)
        ):
            found.append("with target")
        elif isinstance(node, ast.Import | ast.ImportFrom) and any(
            (alias.asname or alias.name).split(".")[0] == name for alias in node.names
        ):
            found.append("import")
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name == name
        ):
            found.append("definition")
        elif isinstance(node, ast.Delete) and any(name in _target_names(t) for t in node.targets):
            found.append("del")
        elif isinstance(node, ast.Global) and name in node.names:
            found.append("global statement")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"globals", "vars", "locals", "exec", "eval"}
        ):
            found.append(f"{node.func.id}()")
        elif isinstance(node, ast.Attribute) and node.attr == "__dict__":
            found.append("__dict__")
    return found


def is_literal_host_set(node: ast.expr) -> bool:
    """``frozenset()`` or ``frozenset({"a", "b"})`` (string constants only)."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "frozenset"
        and not node.keywords
    ):
        return False
    if not node.args:
        return True
    (arg,) = node.args if len(node.args) == 1 else (None,)
    return isinstance(arg, ast.Set) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in arg.elts
    )


def test_the_media_hosts_are_a_literal_assigned_once() -> None:
    (value,) = assignments(egress_tree(), "MOWL_MEDIA_HOSTS")
    assert is_literal_host_set(value)
    # ...and changed nowhere else: not by ``|=``, a walrus, ``globals()`` or any other write.
    assert rebinds(egress_tree(), "MOWL_MEDIA_HOSTS") == ["assign"]
    assert rebinds(egress_tree(), "MOWL_MEDIA_ALLOWLIST") == ["assign"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("H = frozenset()", ["assign"]),
        ("H: frozenset[str] = frozenset()", ["assign"]),
        ("H = frozenset()\nH |= {'a.example'}", ["assign", "augmented assignment"]),
        ("H = frozenset()\nH = H | other", ["assign", "assign"]),
        ("H = frozenset()\nprint((H := other))", ["assign", "walrus"]),
        ("H = frozenset()\nglobals()['H'] = other", ["assign", "globals()"]),
        ("H = frozenset()\nglobals().update(H=other)", ["assign", "globals()"]),
        ("H = frozenset()\nvars()['H'] = other", ["assign", "vars()"]),
        ("H = frozenset()\nmodule.__dict__['H'] = x", ["assign", "__dict__"]),
        (
            "H = frozenset()\ndef f():\n    global H\n    H = other",
            ["assign", "global statement", "assign"],
        ),
        ("H = frozenset()\nfor H in other: pass", ["assign", "loop target"]),
        ("H = frozenset()\nfrom elsewhere import H", ["assign", "import"]),
        ("H = frozenset()\nexec('H = x')", ["assign", "exec()"]),
        ("H = frozenset()\nOTHER |= {1}", ["assign"]),
    ],
)
def test_the_rebind_check_catches_every_way_to_change_the_constant(
    source: str, expected: list[str]
) -> None:
    assert sorted(rebinds(ast.parse(source), "H")) == sorted(expected)


def test_the_literal_check_rejects_every_other_source() -> None:
    for source in (
        "frozenset(os.environ['H'].split(','))",
        "frozenset(read_hosts())",
        "frozenset({host})",
        "frozenset({'a.example'} | EXTRA)",
        "frozenset(['a.example'])",
        "set()",
        "WORKER_HOSTS",
        "frozenset(settings.hosts)",
    ):
        assert not is_literal_host_set(ast.parse(source, mode="eval").body)
    for source in ("frozenset()", "frozenset({'media.example.com'})"):
        assert is_literal_host_set(ast.parse(source, mode="eval").body)


def test_the_media_allowlist_is_built_from_the_constant_alone() -> None:
    (value,) = assignments(egress_tree(), "MOWL_MEDIA_ALLOWLIST")
    assert ast.unparse(value) == "Allowlist(MOWL_MEDIA_HOSTS)"
    assert egress.MOWL_MEDIA_ALLOWLIST.hosts == egress.MOWL_MEDIA_HOSTS


def test_the_client_factory_defaults_to_that_list_and_reads_nothing_else() -> None:
    tree = egress_tree()
    (factory,) = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "mowl_media_async_client"
    ]
    assert ast.unparse(factory.args.defaults[0]) == "MOWL_MEDIA_ALLOWLIST"
    body = " ".join(ast.unparse(statement) for statement in factory.body[1:])  # not the docstring
    for forbidden in ("environ", "getenv", "open(", "settings", "read_text", "load"):
        assert forbidden not in body
    # Nothing in egress reads the environment or a file at all: a list cannot be widened there.
    text = EGRESS_FILE.read_text(encoding="utf-8")
    assert "os.environ" not in text
    assert "getenv" not in text


def test_no_other_source_names_the_media_list() -> None:
    names = ("MOWL_MEDIA_HOSTS", "MOWL_MEDIA_ALLOWLIST")
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path != EGRESS_FILE and any(n in path.read_text(encoding="utf-8") for n in names)
    ]
    assert offenders == []


def test_the_media_hosts_are_empty_until_the_owner_confirms_them() -> None:
    # The owner's PR that adds the confirmed host(s) replaces this with a test pinning exactly them.
    assert frozenset() == egress.MOWL_MEDIA_HOSTS


def test_the_production_lists_are_unchanged_and_hold_no_media_host() -> None:
    assert egress.WORKER_HOSTS == WORKER_HOSTS_BEFORE
    assert egress.WEB_URLLIB_HOSTS == WEB_HOSTS_BEFORE
    for hosts in (egress.WORKER_HOSTS, egress.WEB_URLLIB_HOSTS, egress.SPOTIFY_HOSTS):
        assert not (hosts & egress.MOWL_MEDIA_HOSTS)
    assert egress.worker_hosts(egress.MODEL_HOSTS) & egress.MOWL_MEDIA_HOSTS == frozenset()
    assert "cloud.swedebeat.se" not in egress.WORKER_HOSTS | egress.WEB_URLLIB_HOSTS


def test_the_media_hosts_are_exact_https_names_never_a_wildcard_or_a_cloud_domain() -> None:
    # Vacuous while empty; it binds the owner's PR. ``Allowlist`` already refuses upper case.
    for host in egress.MOWL_MEDIA_HOSTS:
        assert re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", host)
        assert "*" not in host
        assert not host.endswith((".amazonaws.com", ".cloudfront.net", ".s3.amazonaws.com"))
        assert host.count(".") >= 2


# --- no AWS ----------------------------------------------------------------------------------

AWS_SDK = re.compile(
    r"(?i)\b(boto3?|botocore|aiobotocore|aioboto3|s3transfer|s3fs|smart_open|requests-aws4auth|"
    r"aws-sdk|@aws-sdk)\b"
)
AWS_SIGNING = re.compile(
    r"(?i)(AWS4-HMAC-SHA256|aws4_request|AWSAccessKeyId|x-amz-(?:signature|credential|security-token)|"
    r"aws_access_key_id|aws_secret_access_key|aws_session_token)"
)
ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
SKIP_SUFFIXES = {".png", ".ico", ".woff2", ".whl", ".zip", ".gz", ".jpg", ".svg"}


def tracked() -> list[str]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git is not available here")
    out = subprocess.run(  # noqa: S603 - fixed arguments
        [git, "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    ).stdout
    return [n for n in out.decode().split("\0") if n]


def scanned() -> list[tuple[str, str]]:
    """The tracked text files that are code or configuration or dependency locks (not prose:
    ``docs/`` and Markdown explain the ban and name what is forbidden)."""
    files: list[tuple[str, str]] = []
    for name in tracked():
        path = ROOT / name
        if (
            name.startswith("docs/")
            or path.suffix == ".md"
            or path.suffix in SKIP_SUFFIXES
            or path.resolve() == THIS
            or not path.is_file()
        ):
            continue
        try:
            files.append((name, path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue
    return files


def test_no_file_imports_or_names_an_aws_sdk_signs_with_aws_or_holds_an_access_key() -> None:
    found: list[str] = []
    for name, body in scanned():
        for label, pattern in (
            ("sdk", AWS_SDK),
            ("signing", AWS_SIGNING),
            ("key", ACCESS_KEY),
        ):
            if pattern.search(body):
                found.append(f"{name}: {label}")
    assert found == []


def test_the_aws_patterns_catch_what_they_are_for() -> None:
    assert AWS_SDK.search("import boto3")
    assert AWS_SDK.search('"botocore==1.0"')
    for named in ("aioboto3", "s3fs", "smart_open", "requests-aws4auth", "import s3fs"):
        assert AWS_SDK.search(named)
    assert not AWS_SDK.search("font-family: Roboto, sans-serif")
    assert not AWS_SDK.search("smart_opener, s3fsx")
    assert AWS_SIGNING.search("Authorization: AWS4-HMAC-SHA256 Credential=")
    assert AWS_SIGNING.search("?X-Amz-Signature=abc")
    assert AWS_SIGNING.search("aws_secret_access_key = x")
    assert AWS_SIGNING.search("20260101/eu-north-1/s3/aws4_request")
    assert AWS_SIGNING.search("?AWSAccessKeyId=abc&Expires=1")
    assert ACCESS_KEY.search("AKIA" + "ABCDEFGHIJKLMNOP")
    assert not ACCESS_KEY.search("AKIA-short")
