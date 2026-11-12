"""The non-secret field groups that ``Settings`` inherits (#812) stay pure and hold no secret."""

import ast
import typing
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings

from classbeat import config
from classbeat.config import SECRET_FILE_FIELDS, Settings
from classbeat.config_accounts import AccountSettings
from classbeat.config_agent import AgentSettings
from classbeat.config_features import FeatureSettings
from classbeat.config_jobs import JobSettings
from classbeat.config_mail import MailSettings

SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat"
GROUPS = (AccountSettings, MailSettings, JobSettings, AgentSettings, FeatureSettings)
# What a field-group module may import: pure helpers only (no db, network or file access).
ALLOWED_IMPORTS = frozenset(
    {
        "ipaddress",
        "re",
        "typing",
        "pathlib",  # ``Path`` as a field type only
        "pydantic",
        "pydantic_settings",
        "classbeat.config_hosts",
        "classbeat.config_turnstile",
        "classbeat.emailnorm",
    }
)
# Model validators run in this order (the first to raise wins); the split keeps it.
MODEL_VALIDATORS = (
    "_check_turnstile",
    "_plain_smtp_only_locally",
    "_check_smtp_ca_file",
    "_check_billing",
    "_spotify_needs_a_loopback_address",
    "_check_passkeys",
    "_prod_needs_https",
    "_prod_trusts_only_private_proxies",
)


def _mentions_secret(annotation: object) -> bool:
    if annotation is SecretStr:
        return True
    return any(_mentions_secret(arg) for arg in typing.get_args(annotation))


@pytest.mark.parametrize("group", GROUPS, ids=lambda g: g.__name__)
def test_a_field_group_declares_no_secret(group: type) -> None:
    fields = group.model_fields  # type: ignore[attr-defined]
    assert [name for name, f in fields.items() if _mentions_secret(f.annotation)] == []
    assert not set(fields) & set(SECRET_FILE_FIELDS)


def test_every_secret_field_is_declared_in_config_py() -> None:
    own = Settings.__annotations__
    secret_fields = {n for n, f in Settings.model_fields.items() if _mentions_secret(f.annotation)}
    assert set(SECRET_FILE_FIELDS) <= secret_fields
    assert secret_fields <= set(own)


def test_settings_has_every_group_field_once() -> None:
    names = [name for group in GROUPS for name in group.model_fields]
    assert len(names) == len(set(names))
    assert not set(names) & set(Settings.__annotations__)
    assert set(names) <= set(Settings.model_fields)


@pytest.mark.parametrize("group", GROUPS, ids=lambda g: g.__name__)
def test_a_field_group_adds_no_config_or_model_validator(group: type) -> None:
    assert group.model_config == BaseSettings.model_config  # type: ignore[attr-defined]
    assert not group.__pydantic_decorators__.model_validators  # type: ignore[attr-defined]


def test_model_validators_keep_their_order() -> None:
    assert tuple(Settings.__pydantic_decorators__.model_validators) == MODEL_VALIDATORS


@pytest.mark.parametrize("group", GROUPS, ids=lambda g: g.__name__)
def test_a_field_group_module_is_pure(group: type) -> None:
    path = SRC / f"{group.__module__.rsplit('.', 1)[-1]}.py"
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            imported.add(node.module)
    assert imported <= ALLOWED_IMPORTS


def test_the_moved_constants_are_still_exported() -> None:
    assert config.CONNECTION_KEK_ID.fullmatch("kek-1")
    assert config.SUPPORT_ADDRESS.fullmatch("support@classbeat.eu")
