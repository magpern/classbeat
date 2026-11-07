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

# Every manifest language but English, so a new code cannot be skipped by the completeness,
# placeholder, link and golden tests below (the list used to be written out by hand).
MANIFEST = locales.SUPPORTED_LOCALES
TRANSLATED = tuple(code for code in MANIFEST if code != DEFAULT)
KINDS = list(REGISTRY.values())
ids = [k.id for k in KINDS]


@pytest.fixture
def three_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every manifest language is offered, whatever an earlier test patched."""
    monkeypatch.setattr(locales, "SUPPORTED_LOCALES", MANIFEST)


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


def placeholders(text: str) -> list[tuple[str, str | None, str | None]]:
    """Every replacement field, unnamed ones (``{}``, ``{!r}``, ``{:>9}``) included."""
    return [(f, spec, conv) for _, f, spec, conv in string.Formatter().parse(text) if f is not None]


def not_bare(text: str) -> list[str]:
    """What is wrong with the fields of ``text``: only a bare identifier is allowed."""
    wrong: list[str] = []
    for name, spec, conversion in placeholders(text):
        if not re.fullmatch(r"[a-z_]+", name):
            wrong.append(f"name {name!r}")  # unnamed, attribute access or index
        if spec:
            wrong.append(f"format spec {spec!r}")
        if conversion is not None:
            wrong.append(f"conversion {conversion!r}")
    return wrong


@pytest.mark.parametrize("code", TRANSLATED)
def test_the_placeholders_are_the_english_ones_and_bare(code: str) -> None:
    for string_id, text in LOCALES[code].items():
        found = sorted(f for f, _, _ in placeholders(text))
        assert found == sorted(f for f, _, _ in placeholders(en.CATALOGUE[string_id])), string_id
        assert not not_bare(text), (code, string_id)


@pytest.mark.parametrize(
    "text",
    ["a {} b", "a {!r} b", "a {:>999999999} b", "{provider!s}", "{provider:>9}", "{p.x}", "{p[0]}"],
)
def test_the_placeholder_check_catches_unnamed_and_formatted_fields(text: str) -> None:
    assert not_bare(text)


def test_the_placeholder_check_accepts_bare_identifiers() -> None:
    assert not not_bare("x {provider} y {names} {{literal}}")


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
    assert mail_locale("sv") == "sv"  # the manifest offers every language
    assert mail_locale("fr") == "en"
    monkeypatch.setattr(locales, "SUPPORTED_LOCALES", ("en",))
    assert mail_locale("sv") == "en"  # a locale the manifest does not offer falls back
    monkeypatch.setattr(locales, "SUPPORTED_LOCALES", MANIFEST)
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
    def links(text: str, lang: str) -> set[str]:
        found = set(re.findall(r"https?://[^\s\"<>]+", text))
        # the page links carry the mail's own language (ADR 0027 section 7): compare the rest
        return {re.sub(rf"^(https?://[^/]+)/{lang}(?=[/#]|$)", r"\1/LANG", u) for u in found}

    english, other = sample_message(kind, DEFAULT), sample_message(kind, code)
    assert links(other.body, code) == links(english.body, DEFAULT)
    assert other.subject != english.subject
    assert "{" not in other.body
    assert "}" not in other.body


@pytest.mark.parametrize("code", TRANSLATED)
async def test_the_header_names_the_language_of_the_mail(code: str, three_languages: None) -> None:
    async with SmtpFake() as smtp:
        s = Settings(
            smtp_host="127.0.0.1",
            smtp_port=smtp.port,
            smtp_tls="none",
            smtp_sender="classbeat@example.test",
            public_base_url="https://classbeat.example.test",
            locales=",".join(MANIFEST),  # every language, on by default or not
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
    assert "Hvis du vil vælge en ny adgangskode" in mail.text  # the body, not only the subject
    assert "Vælg en ny adgangskode" in mail.html
    assert "If you want to choose a new password" not in mail.text


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


ESSENTIAL = [k.id for k in KINDS if k.essential]


@pytest.mark.parametrize("kind_id", ESSENTIAL)
async def test_an_essential_kind_never_looks_up_a_user(
    kind_id: str, monkeypatch: pytest.MonkeyPatch, three_languages: None
) -> None:
    """No prefs and no locale given: the send path still reads no user (ADR 0022 section 1)."""
    from classbeat.emails import dispatch

    sent: list[Any] = []

    async def capture(mailer: object, message: Any) -> None:
        sent.append(message)

    async def lookup(recipient: str) -> MailPrefs | None:
        raise AssertionError("an essential kind must not look up a user")

    monkeypatch.setattr(dispatch, "send_logged", capture)
    assert await send(
        object(),  # type: ignore[arg-type]
        golden_settings(),
        kind_id,
        "x@example.test",
        REGISTRY[kind_id].sample,
        lookup=lookup,
    )
    assert [m.locale for m in sent] == ["en"]


async def test_an_essential_kind_with_a_given_locale_reads_no_user_either(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    calls: list[str] = []

    async def lookup(recipient: str) -> MailPrefs | None:
        calls.append(recipient)
        return None

    assert await send(
        menv.app.state.mailer,
        menv.settings,
        "account_exists",
        "x@example.com",
        {},
        lookup=lookup,
        locale="sv",
    )
    assert calls == []
    assert decoded(smtp.to("x@example.com")[-1]) == subject_of("account_exists", "sv")


# --- the pre-account provider paths ------------------------------------------------------------


async def pending_row(
    oenv: Env, fake: FakeProviders, locale: str, browser: Browser | None = None
) -> Browser:
    """A browser holding a hand-off row (the provider gave no verified address) in ``locale``."""
    browser = browser or Browser(oenv)
    started = await start(
        browser, "microsoft", "sign_up", invite_code=await make_invite(oenv), locale=locale
    )
    assert location(await browser.get(fake.complete(started.json()["authorization_url"])))
    assert await oenv.scalar("SELECT locale FROM pending_signups") == locale
    return browser


async def post_email(
    oenv: Env, browser: Browser, email: str, body_locale: str | None, header: str | None = None
) -> None:
    from authtools import ORIGIN

    headers = {"Origin": ORIGIN, "X-CSRF-Token": await browser.csrf(), **browser._cookie_header()}
    if header:
        headers["Accept-Language"] = header
    body: dict[str, Any] = {"email": email}
    if body_locale:
        body["locale"] = body_locale
    oenv.client.cookies.clear()
    response = await oenv.client.post("/api/auth/oauth/pending/email", json=body, headers=headers)
    assert response.status_code == 202, response.text


@pytest.mark.parametrize(
    ("body_locale", "header", "expected"),
    [
        ("sv", None, "sv"),  # the request's field, not the row's language
        ("sv", "da", "sv"),
        (None, "sv", "sv"),  # the request's header, not the row's language (plan section 2)
        (None, None, "en"),  # nothing in the request: English, not the row's language
    ],
)
async def test_account_exists_on_the_hand_off_step_follows_the_request_not_the_row(
    oenv: Env,
    fake: FakeProviders,
    smtp: SmtpFake,
    three_languages: None,
    body_locale: str | None,
    header: str | None,
    expected: str,
) -> None:
    await oenv.user("taken@example.com")
    browser = await pending_row(oenv, fake, "da")
    await post_email(oenv, browser, "taken@example.com", body_locale, header)
    [mail] = smtp.to("taken@example.com")
    assert decoded(mail) == subject_of("account_exists", expected)
    assert mail.message["Content-Language"] == expected


@pytest.mark.parametrize(
    ("body_locale", "header", "expected"),
    [
        ("sv", None, "sv"),  # the request's field comes before the row's language
        ("sv", "da", "sv"),
        (None, None, "da"),  # no field: the row's language
        (None, "sv", "da"),  # the row's language comes before the header (ML2)
    ],
)
async def test_the_provider_confirmation_prefers_the_request_then_the_row(
    oenv: Env,
    fake: FakeProviders,
    smtp: SmtpFake,
    three_languages: None,
    body_locale: str | None,
    header: str | None,
    expected: str,
) -> None:
    browser = await pending_row(oenv, fake, "da")  # one unfilled row: this POST sends the mail
    await post_email(oenv, browser, "fresh@example.com", body_locale, header)
    [mail] = smtp.to("fresh@example.com")
    assert decoded(mail) == subject_of("provider_signup_confirm", expected)
    assert mail.message["Content-Language"] == expected


def record_notices(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Run every background notice as usual and keep the ``Notice`` it was built from."""
    from classbeat.emails import notices

    seen: list[Any] = []
    original = notices.send_notice

    async def recording(request_app: Any, notice: Any) -> None:
        seen.append(notice)
        await original(request_app, notice)

    monkeypatch.setattr(notices, "send_notice", recording)
    return seen


async def test_the_welcome_at_a_verified_provider_sign_up_carries_the_flow_language(
    oenv: Env,
    fake: FakeProviders,
    smtp: SmtpFake,
    three_languages: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = record_notices(monkeypatch)
    browser = Browser(oenv)
    started = await start(
        browser, "google", "sign_up", invite_code=await make_invite(oenv), locale="sv"
    )
    assert await oenv.scalar("SELECT locale FROM oauth_flows") == "sv"
    assert location(await browser.get(fake.complete(started.json()["authorization_url"])))
    [welcome] = [n for n in seen if n.kind_id == "welcome"]
    assert welcome.locale == "sv"  # from oauth_flows.locale, not a second lookup
    [mail] = [m for m in smtp.messages if decoded(m) == subject_of("welcome", "sv")]
    assert mail.message["Content-Language"] == "sv"


async def test_the_welcome_after_a_hand_off_carries_the_row_language(
    oenv: Env,
    fake: FakeProviders,
    smtp: SmtpFake,
    three_languages: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = record_notices(monkeypatch)
    browser = await pending_row(oenv, fake, "da")
    await post_email(oenv, browser, "fresh@example.com", None)
    token = smtp.to("fresh@example.com")[-1].token()
    done = await browser.post("/api/auth/signup/confirm", {"token": token})
    assert done.status_code == 200, done.text
    [welcome] = [n for n in seen if n.kind_id == "welcome"]
    assert welcome.locale == "da"
    assert decoded(smtp.to("fresh@example.com")[-1]) == subject_of("welcome", "da")


async def test_a_flow_language_turned_off_before_the_callback_sends_the_welcome_in_english(
    oenv: Env,
    fake: FakeProviders,
    smtp: SmtpFake,
    three_languages: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Browser(oenv)
    started = await start(
        browser, "google", "sign_up", invite_code=await make_invite(oenv), locale="sv"
    )
    assert await oenv.scalar("SELECT locale FROM oauth_flows") == "sv"
    monkeypatch.setattr(oenv.settings, "locales", "da")  # the operator turns Swedish off
    assert location(await browser.get(fake.complete(started.json()["authorization_url"])))
    [mail] = [m for m in smtp.messages if decoded(m) == subject_of("welcome", "en")]
    assert mail.message["Content-Language"] == "en"
    assert not [m for m in smtp.messages if decoded(m) == subject_of("welcome", "sv")]


async def test_a_row_language_turned_off_before_the_confirmation_sends_the_welcome_in_english(
    oenv: Env,
    fake: FakeProviders,
    smtp: SmtpFake,
    three_languages: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = await pending_row(oenv, fake, "da")
    await post_email(oenv, browser, "fresh@example.com", None)
    token = smtp.to("fresh@example.com")[-1].token()
    monkeypatch.setattr(oenv.settings, "locales", "sv")  # the operator turns Danish off
    done = await browser.post("/api/auth/signup/confirm", {"token": token})
    assert done.status_code == 200, done.text
    assert decoded(smtp.to("fresh@example.com")[-1]) == subject_of("welcome", "en")


# --- account routes: the user's stored language ------------------------------------------------


async def test_the_email_change_mails_follow_the_stored_language(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    from flows import signed_in

    browser = await signed_in(menv)
    await menv.user("taken@example.com")
    await menv.run("UPDATE users SET locale = 'da' WHERE email = 'user@example.com'")
    new = "new.address@example.com"
    assert (await browser.post("/api/auth/email/change", {"new_email": new})).status_code == 202
    await browser.post("/api/auth/email/change", {"new_email": "taken@example.com"})
    assert decoded(smtp.to(new)[-1]) == subject_of("email_change_confirm", "da")
    assert decoded(smtp.to("taken@example.com")[-1]) == subject_of("email_change_taken", "da")
    done = await browser.post("/api/auth/email/confirm", {"token": smtp.to(new)[-1].token()})
    assert done.status_code == 200, done.text
    [notice] = smtp.to("user@example.com")
    assert decoded(notice) == subject_of("email_changed", "da")


async def test_the_keys_wipe_mail_follows_the_stored_language(
    menv: Env, three_languages: None
) -> None:
    from uuid import uuid4

    from classbeat.auth.operator import WipeEvent, wipe_mail

    user_id = await menv.user("wiped@example.com")
    await menv.run("UPDATE users SET locale = 'sv' WHERE id = :u", u=user_id)
    event = WipeEvent(uuid4(), user_id, "wiped@example.com", ("spotify",), False)
    async with menv.db() as db:
        message = await wipe_mail(menv.settings, db, event)
    assert message.locale == "sv"
    assert message.subject == subject_of("connections_wiped", "sv")


async def test_the_account_deleted_mail_follows_the_language_read_before_the_cascade(
    menv: Env, smtp: SmtpFake, three_languages: None
) -> None:
    from classbeat.emails.notices import read_prefs

    user_id = await menv.user("gone@example.com")
    await menv.run("UPDATE users SET locale = 'da' WHERE id = :u", u=user_id)
    async with menv.db() as db:
        prefs = await read_prefs(db, user_id)
    assert prefs.locale == "da"
    assert prefs.user_id is None  # no unsubscribe link for an account that is going
    assert await send(
        menv.app.state.mailer,
        menv.settings,
        "account_deleted",
        "gone@example.com",
        REGISTRY["account_deleted"].sample,
        prefs=prefs,
    )
    assert decoded(smtp.to("gone@example.com")[-1]) == subject_of("account_deleted", "da")


async def test_an_account_without_a_language_gets_an_english_deleted_mail(
    menv: Env, three_languages: None
) -> None:
    from classbeat.emails.notices import read_prefs

    user_id = await menv.user("plain@example.com")
    async with menv.db() as db:
        assert (await read_prefs(db, user_id)).locale is None
