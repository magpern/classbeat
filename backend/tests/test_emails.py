"""The branded mail layout, registry and send path (ADR 0022, ME1). No network: ``SmtpFake``."""

import re
from datetime import UTC, datetime
from email.utils import parseaddr
from html.parser import HTMLParser

import pytest

from classbeat.config import Settings
from classbeat.emails import REGISTRY, Category, Kind, MailPrefs, build, send
from classbeat.emails.blocks import Content, Note, Paragraph
from classbeat.emails.catalogue import LOCALES, mail_locale
from classbeat.emails.layout import Branding, Ctx, render_html, render_text
from classbeat.emails.locales.en import CATALOGUE, S, format_when
from classbeat.emails.urls import UnsafeLink, check_link
from classbeat.mail import Mailer
from mailgolden import GOLDEN, golden_settings, sample_message
from smtpfake import SmtpFake

BASE = "https://classbeat.example.test"
SUPPORT = "support@classbeat.eu"
KINDS = list(REGISTRY.values())
ids = [k.id for k in KINDS]


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.srcs: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)
            if name == "src" and value:
                self.srcs.append(value)


def parse(html: str) -> Links:
    parser = Links()
    parser.feed(html)
    return parser


# --- registry and catalogue -------------------------------------------------------------------


def test_the_registry_holds_the_existing_kinds_and_the_new_notices() -> None:
    assert len(REGISTRY) == 22
    assert {k.id for k in KINDS if k.category is Category.WELCOME} == {"welcome"}
    assert {k.id for k in KINDS if k.category is Category.ACCOUNT_ACTIVITY} == {
        "totp_enrolled",
        "mfa_failures",
        "identity_linked",
        "password_changed",
        "account_deleted",
        "identity_unlinked",
        "totp_removed",
        "passkey_added",
        "passkey_removed",
        "passkey_counter_regressed",
    }
    essential = {k.id for k in KINDS if k.essential}
    assert essential == {
        "signup_confirm",
        "provider_signup_confirm",
        "password_reset",
        "mfa_reset_link",  # ADR 0019, #775: the link of ``admin mfa-reset``
        "email_change_confirm",
        "account_exists",
        "email_change_taken",
        "connections_wiped",
        "email_changed",
        "invite_code",  # ADR 0033, #985: the code an admin issued for an invite request
    }
    # ME5 (#656): the one "class ready" kind, optional, switchable by the admin and the user.
    [ready] = [k for k in KINDS if k.category is Category.CLASS_READY]
    assert (ready.id, ready.essential, ready.user_can_opt_out, ready.admin_can_disable) == (
        "class_ready",
        False,
        True,
        True,
    )


@pytest.mark.parametrize("kind", KINDS, ids=ids)
def test_every_kind_is_complete(kind: Kind) -> None:
    k = REGISTRY[kind.id]
    assert isinstance(k.category, Category)
    if k.essential:
        assert k.category is Category.ESSENTIAL
        assert not k.user_can_opt_out
        assert not k.admin_can_disable
    else:
        assert k.category is not Category.ESSENTIAL
    assert k.enabled_by_default
    assert callable(k.builder)
    assert (GOLDEN / f"{k.id}.txt").is_file()
    assert (GOLDEN / f"{k.id}.html").is_file()
    # The sample renders, so every catalogue id it uses exists and every placeholder is filled.
    assert sample_message(k).subject


def test_the_catalogue_has_a_string_for_every_id_and_none_without_an_id() -> None:
    assert set(CATALOGUE) == set(S)
    for string_id, text in CATALOGUE.items():
        assert text.strip(), string_id
        assert "<" not in text, string_id  # the layout escapes; no markup
        assert "&" not in text, string_id


def test_every_catalogue_string_is_used_by_some_kind() -> None:
    """A string no kind renders is dead; render every sample with a recording catalogue."""
    used: set[S] = set()
    original = Ctx.t

    def recording(self: Ctx, string_id: S, **params: object) -> str:
        used.add(string_id)
        return original(self, string_id, **params)

    Ctx.t = recording  # pyright: ignore[reportAttributeAccessIssue]
    try:
        for kind in KINDS:
            sample_message(kind)
        # A branch the sample does not take: the passkeys a reset removed (W5 N4, #797).
        password_changed = REGISTRY["password_changed"]
        build(
            golden_settings(),
            password_changed,
            "a@example.test",
            {**password_changed.sample, "passkeys_removed": 1},
        )
    finally:
        Ctx.t = original  # pyright: ignore[reportAttributeAccessIssue]
    # The sample message carries the unsubscribe line (ME4), so every string is used.
    assert not set(S) - used


def test_the_locale_is_english_without_an_offered_stored_code() -> None:
    assert mail_locale(None) == mail_locale(object()) == mail_locale("fr") == "en"


def test_a_time_is_shown_in_utc() -> None:
    assert format_when(datetime(2026, 11, 6, 12, 30, tzinfo=UTC)) == "6 November 2026, 12:30 UTC"


# --- the subjects of every builder ----------------------------------------------

SUBJECTS = {
    "signup_confirm": "Confirm your classbeat account",
    "provider_signup_confirm": "Confirm your classbeat account",
    "account_exists": "You already have a classbeat account",
    "password_reset": "Reset your classbeat password",
    "email_change_confirm": "Confirm your new classbeat email address",
    "email_change_taken": "A classbeat account already uses this address",
    "email_changed": "Your classbeat email address was changed",
    "invite_code": "Your classbeat invite code",
    "totp_enrolled": "Two-factor authentication was set up on your classbeat account",
    "mfa_failures": "Failed two-factor attempts on your classbeat account",
    "identity_linked": "A sign-in method was added to your classbeat account",
    "connections_wiped": "Your classbeat connections were removed",
    "welcome": "Welcome to classbeat",
    "password_changed": "Your classbeat password was changed",
    "account_deleted": "Your classbeat account was deleted",
    "identity_unlinked": "A sign-in method was removed from your classbeat account",
    "totp_removed": "Two-factor authentication was turned off on your classbeat account",
    "class_ready": "Your classbeat class is ready",  # ME5, new
    "passkey_added": "A passkey was added to your classbeat account",  # ADR 0019, #772
    "passkey_removed": "A passkey was removed from your classbeat account",
    "passkey_counter_regressed": "A passkey on your classbeat account was refused",  # #773
    "mfa_reset_link": "Reset your classbeat password",  # #775: the reset link of mfa-reset
}


def test_the_subjects_are_the_ones_of_the_plain_text_mails() -> None:
    assert {k.id: sample_message(k).subject for k in KINDS} == SUBJECTS


# --- golden files, both parts, same links ----------------------------------------------------


@pytest.mark.parametrize("kind", KINDS, ids=ids)
def test_the_mail_matches_its_golden_files(kind: Kind) -> None:
    k = REGISTRY[kind.id]
    message = sample_message(k)
    assert message.html is not None
    assert message.body == (GOLDEN / f"{k.id}.txt").read_text(encoding="utf-8")
    assert message.html == (GOLDEN / f"{k.id}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_password_changed_names_removed_passkeys_only_when_asked(locale: str) -> None:
    kind = REGISTRY["password_changed"]
    plain = build(golden_settings(), kind, "a@example.test", kind.sample, locale=locale)
    said = build(
        golden_settings(),
        kind,
        "a@example.test",
        {**kind.sample, "passkeys_removed": 2},
        locale=locale,
    )
    assert plain.body != said.body
    assert "passkey" in said.body.lower()
    assert "passkey" not in plain.body.lower()


def urls_in_text(text: str) -> set[str]:
    return set(re.findall(r"https?://\S+", text))


@pytest.mark.parametrize("kind", KINDS, ids=ids)
def test_both_parts_carry_the_same_links(kind: Kind) -> None:
    message = sample_message(REGISTRY[kind.id])
    assert message.html is not None
    page = parse(message.html)
    web = {h for h in page.hrefs if h.startswith("http")}
    assert web == urls_in_text(message.body)
    mailto = {h.removeprefix("mailto:") for h in page.hrefs if h.startswith("mailto:")}
    assert mailto == {SUPPORT}
    assert SUPPORT in message.body


@pytest.mark.parametrize("kind", KINDS, ids=ids)
def test_the_html_is_readable_with_images_off_and_has_no_active_content(kind: Kind) -> None:
    message = sample_message(REGISTRY[kind.id])
    assert message.html is not None
    page = parse(message.html)
    assert page.srcs == [f"{BASE}/email/logo.png"]  # the one image, from our own origin
    assert 'alt="classbeat"' in message.html
    assert not {"script", "link", "iframe", "form", "object"} & set(page.tags)
    assert message.html.count("<img") == 1  # no tracking pixel
    assert "@import" not in message.html  # no remote fonts
    assert "url(" not in message.html
    assert "<table" in message.html
    assert "style=" in message.html


# --- escaping and the URL check ---------------------------------------------------------------


def ctx(footer: str | None = None) -> Ctx:
    return Ctx(BASE, Branding("classbeat", SUPPORT, footer))


def test_text_is_escaped_in_html_and_left_alone_in_the_text_part() -> None:
    evil = '<script>alert(1)</script> & "quoted"'
    content = Content("s", evil, (Paragraph(evil), Note(evil)), evil)
    html = render_html(ctx(footer=evil), content)
    assert "<script>" not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quoted&quot;") >= 5
    assert evil in render_text(ctx(footer=evil), content)


def test_the_admin_footer_is_plain_text_in_both_parts() -> None:
    content = Content("s", "p", (Paragraph("hi"),), "why")
    footer = "Made by <b>us</b>"
    assert "Made by &lt;b&gt;us&lt;/b&gt;" in render_html(ctx(footer), content)
    assert footer in render_text(ctx(footer), content)


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/signin",
        f"{BASE}/reset#token=abc-_9",
        f"{BASE}",
        f"{BASE}/",
        "mailto:" + SUPPORT,
    ],
)
def test_the_url_check_accepts_our_own_links(url: str) -> None:
    assert check_link(url, base_url=BASE, support_address=SUPPORT) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://classbeat.example.test.attacker.example/x",
        "https://attacker.example/classbeat.example.test",
        "https://classbeat.example.test@attacker.example/",
        "https://user:pw@classbeat.example.test/",
        "https://classbeat.example.test:8443/x",
        "http://classbeat.example.test/x",
        "//classbeat.example.test/x",
        "/relative",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,x",
        "https://classbeat.example.test\\@attacker.example/",
        "https://classbeat.example.test/x y",
        'https://classbeat.example.test/"onmouseover="x',
        "https://classbeat.example.test:notaport/",
        "mailto:attacker@example.test",
        f"mailto:{SUPPORT}?cc=attacker@example.test",
        f"mailto:{SUPPORT},attacker@example.test",
        "",
    ],
)
def test_the_url_check_refuses_everything_else(url: str) -> None:
    with pytest.raises(UnsafeLink) as excinfo:
        check_link(url, base_url=BASE, support_address=SUPPORT)
    assert url not in str(excinfo.value) or url == ""


def test_the_url_check_compares_default_ports() -> None:
    assert check_link("https://h.test:443/x", base_url="https://h.test", support_address=SUPPORT)
    with pytest.raises(UnsafeLink):
        check_link("https://h.test:444/x", base_url="https://h.test", support_address=SUPPORT)
    local = "http://127.0.0.1:8000"
    assert check_link(f"{local}/x", base_url=local, support_address=SUPPORT)
    with pytest.raises(UnsafeLink):
        check_link("http://127.0.0.1:8001/x", base_url=local, support_address=SUPPORT)


def test_a_block_with_a_foreign_link_cannot_be_rendered() -> None:
    from classbeat.emails.blocks import Button

    content = Content("s", "p", (Button("Go", "https://attacker.example/"),), "why")
    with pytest.raises(UnsafeLink):
        render_html(ctx(), content)
    with pytest.raises(UnsafeLink):
        render_text(ctx(), content)


def test_a_token_cannot_smuggle_a_link() -> None:
    settings = golden_settings()
    kind = REGISTRY["password_reset"]
    for token in ('x"><a href="https://attacker.example">', "a b", "x\r\nBcc: a@b.c"):
        with pytest.raises(UnsafeLink):
            build(settings, kind, "to@example.test", {"token": token})


def test_the_support_address_setting_is_a_bare_address() -> None:
    assert Settings().mail_support_address == "support@classbeat.eu"
    assert Settings(mail_support_address="help@example.test").mail_support_address
    for bad in ("Name <a@b.test>", "a@b.test\r\nBcc: x@y.test", "no-at-sign", "a b@c.test", ""):
        with pytest.raises(ValueError, match="mail_support_address"):
            Settings(mail_support_address=bad)


# --- the MIME structure on the wire -----------------------------------------------------------


async def deliver(kind_id: str, params: dict[str, object], **settings: object):
    async with SmtpFake() as smtp:
        s = Settings(
            smtp_host="127.0.0.1",
            smtp_port=smtp.port,
            smtp_tls="none",
            smtp_sender="classbeat@example.test",
            public_base_url=BASE,
            **settings,  # pyright: ignore[reportArgumentType]
        )
        assert await send(Mailer(s), s, kind_id, "to@example.com", params)
    [received] = smtp.messages
    return received


async def test_a_mail_is_multipart_alternative_text_then_html_with_the_headers() -> None:
    mail = await deliver("password_reset", {"token": "tok-1"})
    message = mail.message
    assert message.get_content_type() == "multipart/alternative"
    parts = list(message.walk())[1:]
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert {p.get_content_charset() for p in parts} == {"utf-8"}
    assert not any(p.get_filename() for p in message.walk())  # no attachments
    assert message["From"] == "classbeat <classbeat@example.test>"
    assert mail.sender == "<classbeat@example.test>"
    assert message["Reply-To"] == SUPPORT
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Content-Language"] == "en"
    assert message["Date"]
    assert re.fullmatch(r"<.+@example\.test>", str(message["Message-ID"]))
    assert message["Subject"] == "Reset your classbeat password"
    assert f"{BASE}/en/reset#token=tok-1" in mail.text
    assert f'href="{BASE}/en/reset#token=tok-1"' in mail.html
    assert mail.token() == "tok-1"


async def test_the_from_address_is_the_configured_sender_whatever_the_branding() -> None:
    async with SmtpFake() as smtp:
        s = Settings(
            smtp_host="127.0.0.1",
            smtp_port=smtp.port,
            smtp_tls="none",
            smtp_sender="classbeat@example.test",
            public_base_url=BASE,
        )
        brand = Branding("Evil <evil@attacker.example>", "help@example.test", None)
        await send(Mailer(s), s, "account_exists", "to@example.com", {}, branding=brand)
    assert smtp.messages[0].sender == "<classbeat@example.test>"
    assert parseaddr(smtp.messages[0].message["From"])[1] == "classbeat@example.test"
    assert smtp.messages[0].message["Reply-To"] == "help@example.test"


async def test_a_sender_name_with_a_line_break_is_refused_not_injected() -> None:
    async with SmtpFake() as smtp:
        s = Settings(smtp_host="127.0.0.1", smtp_port=smtp.port, smtp_tls="none")
        brand = Branding("x\r\nBcc: a@b.test", SUPPORT, None)
        await send(Mailer(s), s, "account_exists", "to@example.com", {}, branding=brand)
    assert smtp.messages == []  # logged by class, never sent


async def test_the_support_address_setting_reaches_the_mail() -> None:
    mail = await deliver("account_exists", {}, mail_support_address="help@example.test")
    assert mail.message["Reply-To"] == "help@example.test"
    assert "mailto:help@example.test" in mail.html
    assert "help@example.test" in mail.text
    assert SUPPORT not in mail.text


# --- the one send path ------------------------------------------------------------------------


async def attempt(kind_id: str, **gate: object) -> list[object]:
    async with SmtpFake() as smtp:
        s = Settings(smtp_host="127.0.0.1", smtp_port=smtp.port, smtp_tls="none")
        params = REGISTRY[kind_id].sample
        sent = await send(Mailer(s), s, kind_id, "to@example.com", params, **gate)  # pyright: ignore
    assert sent == bool(smtp.messages)
    return list(smtp.messages)


async def test_essential_kinds_never_look_up_a_user_and_ignore_every_switch() -> None:
    async def lookup(recipient: str) -> MailPrefs | None:
        raise AssertionError("an essential kind must not look up preferences")

    everything_off = MailPrefs(
        unsubscribed_all=True, opted_out=frozenset(c.value for c in Category)
    )
    for kind in KINDS:
        if kind.essential:
            assert await attempt(
                kind.id, lookup=lookup, prefs=everything_off, disabled=frozenset(REGISTRY)
            )


async def test_an_optional_kind_follows_the_admin_switch_and_the_user_preference() -> None:
    on = MailPrefs()
    assert await attempt("totp_enrolled", prefs=on)
    assert not await attempt("totp_enrolled", prefs=on, disabled=frozenset({"totp_enrolled"}))
    assert not await attempt("totp_enrolled", prefs=MailPrefs(unsubscribed_all=True))
    assert not await attempt(
        "totp_enrolled", prefs=MailPrefs(opted_out=frozenset({"account_activity"}))
    )
    assert await attempt("totp_enrolled", prefs=MailPrefs(opted_out=frozenset({"welcome"})))


async def test_an_optional_kind_looks_prefs_up_by_recipient_and_needs_an_account() -> None:
    asked: list[str] = []

    async def known(recipient: str) -> MailPrefs | None:
        asked.append(recipient)
        return MailPrefs()

    async def unknown(recipient: str) -> MailPrefs | None:
        return None

    assert await attempt("mfa_failures", lookup=known)
    assert asked == ["to@example.com"]
    assert not await attempt("mfa_failures", lookup=unknown)
    assert not await attempt("mfa_failures")  # no prefs, no lookup: no account found


async def test_an_optional_kind_with_the_admin_switch_off_does_not_even_look_up() -> None:
    async def lookup(recipient: str) -> MailPrefs | None:
        raise AssertionError("the admin switch comes first")

    assert not await attempt("mfa_failures", lookup=lookup, disabled=frozenset({"mfa_failures"}))
