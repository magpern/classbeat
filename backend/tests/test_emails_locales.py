"""Swedish and Danish account mail (ML6, #671; ADR 0023 sections 2, 5 and 8, ADR 0022).

The catalogues, the list of strings still English, the placeholders, the dates, the golden files
and the language each send path picks. No network: ``SmtpFake``."""

import re
import string
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from typing import Any

import pytest

from authtools import ORIGIN, Env
from classbeat import locales
from classbeat.config import Settings
from classbeat.emails import REGISTRY, Kind, MailPrefs, build, send
from classbeat.emails.catalogue import DEFAULT, LOCALES, format_when, known, mail_locale, translator
from classbeat.emails.locales import da, en, sv
from classbeat.emails.locales.en import S
from classbeat.emails.locales.untranslated import UNTRANSLATED
from classbeat.mail import Mailer
from flows import PASSWORD, Browser, user_with_password
from mailgolden import golden_dir, golden_settings, sample_message
from oauthtools import location, make_invite, start
from oauthtools import oenv as oenv
from oidcfake import FakeProviders
from smtpfake import SmtpFake
from test_auth_reset import EMAIL, ask
from test_auth_signup import invite as signup_invite

TRANSLATED = ("sv", "da")
KINDS = list(REGISTRY.values())
ids = [k.id for k in KINDS]


@pytest.fixture
def three_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest lists only English until ML7; the path tests need three."""
    monkeypatch.setattr(locales, "SUPPORTED_LOCALES", ("en", "sv", "da"))


# --- the catalogues ----------------------------------------------------------------------------


def test_the_untranslated_list_is_empty_at_merge() -> None:
    assert frozenset() == UNTRANSLATED


@pytest.mark.parametrize("code", TRANSLATED)
def test_every_string_is_translated_or_listed(code: str) -> None:
    missing = {s for s in S if s not in LOCALES[code]}
    assert missing == set(UNTRANSLATED), "a missing string must be listed, and only those"


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_listed_string_is_not_translated_and_exists(code: str) -> None:
    assert not UNTRANSLATED & set(LOCALES[code])
    assert set(S) >= UNTRANSLATED


@pytest.mark.parametrize("code", TRANSLATED)
def test_the_placeholders_are_the_english_ones_and_bare(code: str) -> None:
    def parse(text: str) -> list[tuple[str, str | None, str | None]]:
        return [(f, spec, conv) for _, f, spec, conv in string.Formatter().parse(text) if f]

    for string_id, text in LOCALES[code].items():
        found = parse(text)
        expected = parse(en.CATALOGUE[string_id])
        assert sorted(f for f, _, _ in found) == sorted(f for f, _, _ in expected)
        for name, spec, conversion in found:
            assert re.fullmatch(r"[a-z_]+", name), (code, string_id)
            assert not spec, (code, string_id)
            assert conversion is None, (code, string_id)


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_catalogue_holds_no_string_the_english_one_lacks(code: str) -> None:
    assert set(LOCALES[code]) <= set(S)


def test_a_missing_string_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    trimmed = {k: v for k, v in sv.CATALOGUE.items() if k is not S.FOOTER_SITE}
    monkeypatch.setitem(LOCALES, "sv", trimmed)
    assert translator("sv")(S.FOOTER_SITE) == en.CATALOGUE[S.FOOTER_SITE]


def test_an_unknown_locale_is_english() -> None:
    assert known("fr") == known(None) == known(7) == known("sv ") == DEFAULT
    assert known("sv") == "sv"
    assert translator("zz")(S.FOOTER_SITE) == en.CATALOGUE[S.FOOTER_SITE]


# --- the dates ---------------------------------------------------------------------------------

WHEN = datetime(2026, 3, 5, 7, 4, tzinfo=UTC)


def test_a_time_is_written_by_hand_in_each_language() -> None:
    assert format_when("en", WHEN) == "5 March 2026, 07:04 UTC"
    assert format_when("sv", WHEN) == "5 mars 2026 kl. 07:04 UTC"
    assert format_when("da", WHEN) == "5. marts 2026 kl. 07:04 UTC"


def test_a_time_in_another_zone_is_shown_in_utc() -> None:
    from datetime import timezone

    local = datetime(2026, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=2)))
    assert format_when("sv", local) == "31 december 2026 kl. 21:30 UTC"
    assert format_when("da", local) == "31. december 2026 kl. 21:30 UTC"


def test_every_month_has_a_name() -> None:
    for module in (en, sv, da):
        assert len(module.MONTHS) == 12
        assert all(module.MONTHS)


# --- mail_locale -------------------------------------------------------------------------------


def test_the_stored_language_is_used_only_when_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    assert mail_locale("sv") == "en"  # the manifest offers only English until ML7
    monkeypatch.setattr(locales, "SUPPORTED_LOCALES", ("en", "sv", "da"))
    assert mail_locale("sv") == "sv"
    assert mail_locale("da") == "da"
    assert mail_locale(None) == mail_locale("fr") == mail_locale(5) == "en"
    assert mail_locale(type("U", (), {"locale": "da"})()) == "da"
    assert mail_locale("sv", Settings(_env_file=None, locales="da")) == "en"  # type: ignore[call-arg]
    assert mail_locale("da", Settings(_env_file=None, locales="da")) == "da"  # type: ignore[call-arg]


def test_every_manifest_code_has_a_catalogue() -> None:
    from classbeat.locales import SUPPORTED_LOCALES

    assert set(SUPPORTED_LOCALES) <= set(LOCALES)


# --- every kind, every language ----------------------------------------------------------------


@pytest.mark.parametrize("code", TRANSLATED)
@pytest.mark.parametrize("kind", KINDS, ids=ids)
def test_the_mail_matches_its_golden_files(kind: Kind, code: str) -> None:
    message = sample_message(kind, code)
    assert message.html is not None
    folder = golden_dir(code)
    assert message.body == (folder / f"{kind.id}.txt").read_text(encoding="utf-8")
    assert message.html == (folder / f"{kind.id}.html").read_text(encoding="utf-8")
    assert message.locale == code
    assert f'lang="{code}"' in message.html


@pytest.mark.parametrize("code", TRANSLATED)
@pytest.mark.parametrize("kind", KINDS, ids=ids)
def test_a_translated_mail_carries_the_same_links_as_the_english_one(kind: Kind, code: str) -> None:
    def links(text: str) -> set[str]:
        return set(re.findall(r"https?://[^\s\"<>]+", text))

    english, other = sample_message(kind, DEFAULT), sample_message(kind, code)
    assert links(other.body) == links(english.body)
    assert other.subject != english.subject
    assert "{" not in other.body
    assert "}" not in other.body


@pytest.mark.parametrize("code", TRANSLATED)
async def test_the_header_names_the_language_of_the_mail(code: str) -> None:
    async with SmtpFake() as smtp:
        s = Settings(
            smtp_host="127.0.0.1",
            smtp_port=smtp.port,
            smtp_tls="none",
            smtp_sender="classbeat@example.test",
            public_base_url="https://classbeat.example.test",
        )
        assert await send(
            Mailer(s), s, "password_reset", "to@example.com", {"token": "t"}, locale=code
        )
    [received] = smtp.messages
    assert received.message["Content-Language"] == code


def test_an_unknown_locale_argument_builds_an_english_mail() -> None:
    settings = golden_settings()
    kind = REGISTRY["password_reset"]
    english = build(settings, kind, "a@example.test", kind.sample)
    assert build(settings, kind, "a@example.test", kind.sample, locale="xx") == english
    assert english.locale == "en"


async def test_an_optional_mail_uses_the_stored_language_of_its_prefs(
    monkeypatch: pytest.MonkeyPatch, three_languages: None
) -> None:
    from classbeat.emails import dispatch

    sent: list[Any] = []

    async def capture(mailer: object, message: Any) -> None:
        sent.append(message)

    monkeypatch.setattr(dispatch, "send_logged", capture)
    settings = golden_settings()
    for stored, expected in (("sv", "sv"), ("da", "da"), (None, "en"), ("fr", "en")):
        assert await send(
            object(),  # type: ignore[arg-type]
            settings,
            "welcome",
            "a@example.test",
            REGISTRY["welcome"].sample,
            prefs=MailPrefs(locale=stored),
        )
        assert sent[-1].locale == expected


# --- the send paths ----------------------------------------------------------------------------


def decoded(mail: Any) -> str:
    return str(make_header(decode_header(mail.subject)))


def subject_of(kind_id: str, code: str) -> str:
    return sample_message(REGISTRY[kind_id], code).subject


async def test_the_account_exists_mail_follows_the_request_language(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    await user_with_password(menv)
    browser = Browser(menv)
    response = await browser.post(
        "/api/auth/signup",
        {
            "email": "user@example.com",
            "password": PASSWORD,
            "invite_code": await signup_invite(menv),
            "locale": "sv",
        },
    )
    assert response.status_code == 202, response.text
    [mail] = smtp.to("user@example.com")
    assert decoded(mail) == subject_of("account_exists", "sv")
    assert mail.message["Content-Language"] == "sv"


async def test_the_sign_up_confirmation_follows_the_request_language_then_the_header(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    code = await signup_invite(menv)
    body = {"email": "a@example.com", "password": PASSWORD, "invite_code": code, "locale": "da"}
    assert (await Browser(menv).post("/api/auth/signup", body)).status_code == 202
    assert decoded(smtp.to("a@example.com")[-1]) == subject_of("signup_confirm", "da")
    browser = Browser(menv)
    headers = {"Origin": ORIGIN, "X-CSRF-Token": await browser.csrf(), "Accept-Language": "sv"}
    body = {
        "email": "b@example.com",
        "password": PASSWORD,
        "invite_code": await signup_invite(menv),
    }
    response = await menv.client.post("/api/auth/signup", json=body, headers=headers)
    assert response.status_code == 202, response.text
    assert decoded(smtp.to("b@example.com")[-1]) == subject_of("signup_confirm", "sv")


async def test_the_welcome_follows_the_language_of_the_new_account(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    body = {
        "email": "w@example.com",
        "password": PASSWORD,
        "invite_code": await signup_invite(menv),
        "locale": "sv",
    }
    browser = Browser(menv)
    assert (await browser.post("/api/auth/signup", body)).status_code == 202
    token = smtp.to("w@example.com")[-1].token()
    done = await browser.post("/api/auth/signup/confirm", {"token": token, "password": PASSWORD})
    assert done.status_code == 200, done.text
    mails = smtp.to("w@example.com")
    assert decoded(mails[-1]) == subject_of("welcome", "sv")


async def test_the_reset_mail_follows_the_stored_language_of_the_account(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    await user_with_password(menv)
    await menv.run("UPDATE users SET locale = 'da'")
    assert (await ask(Browser(menv))).status_code == 202
    [mail] = smtp.messages
    assert decoded(mail) == subject_of("password_reset", "da")
    assert mail.message["Content-Language"] == "da"


async def test_an_account_without_a_language_gets_english(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    await user_with_password(menv)
    assert (await ask(Browser(menv), EMAIL)).status_code == 202
    [mail] = smtp.messages
    assert decoded(mail) == subject_of("password_reset", "en")


async def test_a_stored_language_that_is_no_longer_offered_gets_english(
    menv: Env, smtp: SmtpFake, three_languages: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await user_with_password(menv)
    await menv.run("UPDATE users SET locale = 'sv'")
    monkeypatch.setattr(menv.settings, "locales", "da")
    assert (await ask(Browser(menv))).status_code == 202
    [mail] = smtp.messages
    assert decoded(mail) == subject_of("password_reset", "en")


async def test_the_provider_confirmation_follows_the_row_language(
    oenv: Env, fake: FakeProviders, smtp: SmtpFake, three_languages: None
) -> None:
    browser = Browser(oenv)
    started = await start(
        browser, "microsoft", "sign_up", invite_code=await make_invite(oenv), locale="da"
    )
    assert location(await browser.get(fake.complete(started.json()["authorization_url"])))
    await browser.post("/api/auth/oauth/pending/email", {"email": "hand@example.com"})
    [mail] = smtp.to("hand@example.com")
    assert decoded(mail) == subject_of("provider_signup_confirm", "da")
    # A language named in the email step replaces the row's.
    await browser.post(
        "/api/auth/oauth/pending/email", {"email": "other@example.com", "locale": "sv"}
    )


async def test_the_essential_path_still_reads_no_user(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    """A kind with the locale given sends without a prefs lookup (ADR 0022 section 1)."""
    calls: list[str] = []

    async def lookup(recipient: str) -> MailPrefs | None:
        calls.append(recipient)
        return None

    s = menv.settings
    assert await send(
        menv.app.state.mailer, s, "account_exists", "x@example.com", {}, lookup=lookup, locale="sv"
    )
    assert calls == []
    assert decoded(smtp.to("x@example.com")[-1]) == subject_of("account_exists", "sv")
