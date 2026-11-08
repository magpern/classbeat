"""The shipped swag access-log rotation rule keeps the promise of the privacy notice (#826).

The rule is a release asset (``docs/runbooks/swag-classbeat-logrotate.conf``, installed by
``scripts/install-swag-logrotate.sh``). Its numbers must agree with the runbook text and with the
"up to 30 days" of the notice (``privacy.retention.li7``, every language), or IP-bearing logs
outlive what visitors were told.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULE = ROOT / "docs" / "runbooks" / "swag-classbeat-logrotate.conf"
RUNBOOK = ROOT / "docs" / "runbooks" / "vps-deploy.md"
LOG = "/config/log/nginx/classbeat.access.log"
DAYS = 30


def _directives() -> dict[str, str]:
    """The rule's directives (inside the one block), comments and blank lines dropped."""
    lines = [ln.strip() for ln in RULE.read_text(encoding="utf-8").splitlines()]
    body = [ln for ln in lines if ln and not ln.startswith("#")]
    assert body[0] == LOG + " {", body
    assert body[-1] == "}", body
    out: dict[str, str] = {}
    for ln in body[1:-1]:
        key, _, value = ln.partition(" ")
        assert key not in out, f"{key} set twice"
        out[key] = value
    return out


def test_rule_keeps_the_log_at_most_thirty_days() -> None:
    d = _directives()
    # The current file holds one day, 29 rotated files make 30; maxage removes any older one.
    assert d["daily"] == ""
    assert int(d["rotate"]) == DAYS - 1
    assert int(d["maxage"]) == DAYS
    # copytruncate: nginx keeps its file handle, so no reload is needed.
    assert "copytruncate" in d
    assert "missingok" in d


def test_rule_numbers_match_the_runbook() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    d = _directives()
    step = text[text.index("**Access-log retention") : text.index("What the sample does")]
    assert f"`rotate {d['rotate']}`" in step
    assert f"`maxage {d['maxage']}`" in step
    assert f"{d['rotate']} rotated files make {int(d['rotate']) + 1} days" in step
    assert f"Access-log retention: {d['maxage']} days" in step


@pytest.mark.parametrize("lang", ["en", "sv", "da"])
def test_privacy_notice_states_the_same_period(lang: str) -> None:
    src = (ROOT / "frontend" / "src" / "i18n" / f"legal.{lang}.ts").read_text(encoding="utf-8")
    m = re.search(r"'privacy\.retention\.li7':\s*'([^']*)'", src)
    assert m, "privacy.retention.li7 not found"
    assert re.search(rf"\b{DAYS}\b", m.group(1)), m.group(1)


def test_rule_log_is_the_one_the_swag_sample_writes() -> None:
    sample = (ROOT / "docs" / "runbooks" / "swag-classbeat.subdomain.conf").read_text(
        encoding="utf-8"
    )
    assert f"access_log {LOG} " in sample
