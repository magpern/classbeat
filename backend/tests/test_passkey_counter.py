"""The signature counter rule of ADR 0019 section 7 at sign-in (#773).

The rule is ours alone: py_webauthn gets ``credential_current_sign_count=0`` (so its own check can
never fire) and the finish's conditional ``UPDATE`` decides. A regression refuses the sign-in,
records ``passkey_counter_regressed``, mails the user at most once a day per passkey and keeps the
passkey.
"""

from typing import Any

import pytest

from authtools import Env
from classbeat.auth import passkey_assertion
from passkeysignin import SESSIONS, anonymous, failures, sign_in_with_passkey, with_passkey
from passkeytools import count, mails, reason
from passkeytools import penv as penv
from smtpfake import SmtpFake

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

REGRESSED = "SELECT count(*) FROM auth_events WHERE kind = 'passkey_counter_regressed'"
SUBJECT = "A passkey sign-in to your classbeat account was refused"


async def stored_count(env: Env) -> int:
    return await count(env, "SELECT sign_count FROM webauthn_credentials")


async def test_both_zero_is_accepted_every_time(penv: Env) -> None:
    holder = await with_passkey(penv)  # registered at 0, as most synced passkeys
    for _ in range(3):
        assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 200
    assert await stored_count(penv) == 0
    assert await count(penv, REGRESSED) == 0


async def test_a_growing_counter_is_accepted_and_stored(penv: Env) -> None:
    holder = await with_passkey(penv)
    for value in (1, 5, 6):
        holder.device.sign_count = value
        assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 200
        assert await stored_count(penv) == value


@pytest.mark.parametrize(
    ("stored", "sent"),
    [(5, 4), (5, 5), (5, 0)],
    ids=["rollback", "equal-non-zero", "non-zero-to-zero"],
)
async def test_a_counter_that_does_not_go_up_is_refused_recorded_mailed_and_kept(
    penv: Env, smtp: SmtpFake, stored: int, sent: int
) -> None:
    holder = await with_passkey(penv)
    await penv.run("UPDATE webauthn_credentials SET sign_count = :n", n=stored)
    holder.device.sign_count = sent
    response = await sign_in_with_passkey(anonymous(penv), holder)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, SESSIONS) == 0
    assert await stored_count(penv) == stored  # untouched
    assert await count(penv, "SELECT count(*) FROM webauthn_credentials") == 1  # kept
    passkey = await penv.scalar("SELECT id::text FROM webauthn_credentials")
    detail = await penv.scalar(
        "SELECT detail FROM auth_events WHERE kind = 'passkey_counter_regressed'"
    )
    assert detail == {"passkey": passkey, "mailed": True}
    assert await failures(penv, "counter") == 1
    found = [m for m in await mails(smtp, holder.email, 2) if m.subject == SUBJECT]
    assert len(found) == 1
    assert "Passkey " in found[0].text  # the label, never an id or key


async def test_the_regression_mail_goes_out_at_most_once_a_day_per_passkey(
    penv: Env, smtp: SmtpFake
) -> None:
    holder = await with_passkey(penv)
    await penv.run("UPDATE webauthn_credentials SET sign_count = 9")
    holder.device.sign_count = 3
    for _ in range(3):
        assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 400
    assert await count(penv, REGRESSED) == 3  # every regression is recorded
    mailed = (
        "SELECT count(*) FROM auth_events WHERE kind = 'passkey_counter_regressed' "
        "AND detail->>'mailed' = 'true'"
    )
    assert await count(penv, mailed) == 1
    # A day later the next one mails again.
    await penv.run("UPDATE auth_events SET at = now() - interval '25 hours'")
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 400
    assert await count(penv, mailed) == 2
    found = [m for m in await mails(smtp, holder.email, 3) if m.subject == SUBJECT]
    assert len(found) == 2


async def test_the_counter_case_is_decided_by_our_update_with_the_library_given_zero(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The library sees 0 every time, so a rollback passes it and reaches the conditional
    ``UPDATE``, which is what refuses it (the ``passkey_counter_regressed`` event exists only on
    that path)."""
    seen: list[int] = []
    original = passkey_assertion.verify_authentication_response

    def spy(**kwargs: Any) -> Any:
        seen.append(kwargs["credential_current_sign_count"])
        return original(**kwargs)

    monkeypatch.setattr(passkey_assertion, "verify_authentication_response", spy)
    holder = await with_passkey(penv)
    holder.device.sign_count = 7
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 200
    holder.device.sign_count = 2
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 400
    assert seen == [0, 0]
    assert await count(penv, REGRESSED) == 1
    assert await stored_count(penv) == 7


async def test_a_counter_regression_never_disables_the_passkey(penv: Env) -> None:
    holder = await with_passkey(penv)
    await penv.run("UPDATE webauthn_credentials SET sign_count = 4")
    holder.device.sign_count = 4
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 400
    holder.device.sign_count = 5
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 200
