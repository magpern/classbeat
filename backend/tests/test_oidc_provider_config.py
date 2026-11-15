"""``load_provider``: off without settings, and web refuses to start on a half or bad
configuration, naming the file but never its content (ADR 0034 section 1)."""

import hashlib
from pathlib import Path

import pytest

from authtools import make_settings
from classbeat.auth.oidc.provider import OidcConfigError, load_provider
from oidctools import CLIENT_SECRET, OIDC_SETTINGS, PAIRWISE_KEY, write_provider_files

URL = "postgresql+psycopg://u:p@127.0.0.1:1/db"


def test_no_settings_means_the_provider_is_off(tmp_path: Path) -> None:
    assert load_provider(make_settings(URL), tmp_path) is None


@pytest.mark.parametrize("name", ["oidc_player_client_id", "oidc_player_redirect_uri"])
def test_half_a_configuration_is_refused(tmp_path: Path, name: str) -> None:
    write_provider_files(tmp_path)
    with pytest.raises(OidcConfigError, match="both"):
        load_provider(make_settings(URL, **{name: OIDC_SETTINGS[name]}), tmp_path)


def test_a_good_configuration_loads_and_its_repr_hides_the_keys(tmp_path: Path) -> None:
    write_provider_files(tmp_path)
    provider = load_provider(make_settings(URL, **OIDC_SETTINGS), tmp_path)
    assert provider is not None
    assert provider.pairwise_key == PAIRWISE_KEY
    assert provider.secret_matches(CLIENT_SECRET)
    assert not provider.secret_matches(CLIENT_SECRET[:-1] + "x")
    assert not provider.secret_matches(CLIENT_SECRET + "=")
    assert PAIRWISE_KEY.hex() not in repr(provider)
    assert hashlib.sha256(CLIENT_SECRET.encode()).hexdigest() not in repr(provider)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("oidc_client_secret_hash", None),
        ("oidc_client_secret_hash", "PLANTED" + "0" * 57),
        ("oidc_pairwise_key", None),
        ("oidc_pairwise_key", "ABCDEF" * 10 + "PLAN"),
        ("oidc_pairwise_key", "0" * 62),
        ("oidc_signing_keys", None),
        ("oidc_signing_keys", '{"keys": [{"kid": "PLANTED"}]}'),
        ("oidc_signing_keys", "PLANTED" * 20000),
    ],
)
def test_a_missing_or_bad_file_is_refused_without_its_content(
    tmp_path: Path, name: str, content: str | None
) -> None:
    pem = write_provider_files(tmp_path)
    if content is None:
        (tmp_path / name).unlink()
    else:
        (tmp_path / name).write_text(content)
    with pytest.raises(OidcConfigError) as refused:
        load_provider(make_settings(URL, **OIDC_SETTINGS), tmp_path)
    message = str(refused.value)
    assert name in message
    assert "PLANTED" not in message
    assert "ABCDEF" not in message
    assert pem not in message
    assert PAIRWISE_KEY.hex() not in message
