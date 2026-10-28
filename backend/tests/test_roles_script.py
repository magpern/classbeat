"""The password secret files are read by ``10-roles.sql`` and by ``config.py`` the same way."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from classbeat.config import read_secret_files

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "postgres" / "init" / "10-roles.sql"
OWNER, APP = "owner-pw with space", "app-pw"
# The text editors and `echo` leave: LF, CRLF, nothing. Only that one terminator is dropped.
ENDINGS = ["\n", "\r\n", ""]
pytestmark = pytest.mark.usefixtures("role_urls")  # skips (or fails in CI) without a database


def password_lines() -> list[str]:
    lines = SCRIPT.read_text("utf-8").splitlines()
    backslash = chr(92)  # psql meta-commands start with it
    prefixes = tuple(f"{backslash}set {name}_password" for name in ("owner", "app"))
    wanted = [ln for ln in lines if ln.startswith(prefixes)]
    assert len(wanted) == 2
    return wanted


@pytest.mark.parametrize("ending", ENDINGS, ids=["lf", "crlf", "none"])
def test_a_password_file_with_any_line_ending_gives_the_same_password(
    tmp_path: Path, ending: str
) -> None:
    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql is not installed")
    (tmp_path / "owner").write_bytes((OWNER + ending).encode())
    (tmp_path / "app").write_bytes((APP + ending).encode())
    # Same reading as config.py, so both agree.
    secrets_ = read_secret_files(tmp_path, ("owner", "app"))
    assert secrets_ == {"owner": OWNER, "app": APP}

    script = tmp_path / "check.sql"
    script.write_text(
        "\n".join(password_lines())
        + f"\nSELECT CASE WHEN :'owner_password' = '{OWNER}' AND :'app_password' = '{APP}'"
        + " THEN 'same' ELSE 'different' END;\n",
        "utf-8",
    )
    url = make_url(os.environ["CLASSBEAT_TEST_DATABASE_URL"])
    env = {
        **os.environ,
        "DATABASE_OWNER_PASSWORD_FILE": str(tmp_path / "owner"),
        "DATABASE_PASSWORD_FILE": str(tmp_path / "app"),
        "PGPASSWORD": url.password or "",
    }
    result = subprocess.run(  # noqa: S603
        [
            psql,
            "-X",
            "-tA",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            url.host or "",
            "-p",
            str(url.port or 5432),
            "-U",
            url.username or "",
            "-d",
            url.database or "",
            "-f",
            str(script),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, result.stdout.strip()) == (0, "same"), result.stderr
