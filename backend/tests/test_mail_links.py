"""Mail links carry the recipient's language (ADR 0027 section 7, #719). No network, no database.

Page links are ``{base}/{mail_locale}/path#token=...``; the RFC 8058 one-click URL, the
``/email/...`` logo and the bare site link in the footer are never prefixed. The unprefixed forms
that mail sent before this change used stay SPA routes (the client redirects them, ADR 0027
section 3).
"""

import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import pytest

from classbeat import locales
from classbeat.auth import Keys
from classbeat.config import Settings
from classbeat.emails import REGISTRY, Kind, MailPrefs, send
from classbeat.emails.catalogue import LOCALES
from classbeat.emails.dispatch import sample_unsubscribe_links, unsubscribe_links
from classbeat.emails.layout import Branding, Ctx
from classbeat.emails.urls import UnsafeLink, check_link
from classbeat.mail import Mailer
from mailgolden import golden_settings, sample_message
from smtpfake import SmtpFake

BASE = "https://classbeat.example.test"
SUPPORT = "support@classbeat.eu"
KINDS = list(REGISTRY.values())
ids = [k.id for k in KINDS]
APP_TSX = Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.tsx"
UUID_PATH = re.compile(r"/classes/[0-9a-fA-F-]{36}")


def ctx(locale: str) -> Ctx:
    return Ctx(BASE, Branding("classbeat", SUPPORT), locale)


@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_a_page_link_is_prefixed_with_the_mails_language(locale: str) -> None:
    c = ctx(locale)
    assert c.url("/reset", "tok") == f"{BASE}/{locale}/reset#token=tok"
    assert c.url("/signup/confirm", "tok") == f"{BASE}/{locale}/signup/confirm#token=tok"
    assert c.url("/account") == f"{BASE}/{locale}/account"
    assert c.url("/classes/abc") == f"{BASE}/{locale}/classes/abc"
    assert c.url("/") == f"{BASE}/{locale}"  # canonical form: no trailing slash


def test_the_prefix_is_only_ever_a_known_catalogue_language() -> None:
    from classbeat.emails.dispatch import build

    message = build(
        Settings(public_base_url=BASE),
        REGISTRY["password_reset"],
        "a@b.test",
        {"token": "t"},
        locale="fr; evil",
    )
    assert f"{BASE}/en/reset#token=t" in message.body  # an unknown code is English, never echoed
    assert "evil" not in message.body


def app_routes() -> str:
    return APP_TSX.read_text(encoding="utf-8")


def web_urls(text: str) -> set[str]:
    return set(re.findall(r"https?://[^\s\"<>]+", text.replace("&amp;", "&")))


@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_every_path_any_mail_kind_emits_is_prefixed_or_one_of_the_fixed_exceptions(
    locale: str,
) -> None:
    routes = app_routes()
    seen: set[str] = set()
    for kind in KINDS:
        message = sample_message(kind, locale)
        assert message.html is not None
        for url in web_urls(message.body) | web_urls(message.html):
            parts = urlsplit(url)
            assert f"{parts.scheme}://{parts.netloc}" == BASE, url
            path = parts.path
            if path.startswith("/email/"):
                assert path == "/email/logo.png", url  # assets are never prefixed
            elif path == "/api/mail/unsubscribe":
                assert parts.query.startswith("token="), url  # one-click: never prefixed
            elif path == "":
                pass  # the bare site link in the footer: the client redirects it
            else:
                first, _, rest = path[1:].partition("/")
                assert first == locale, (kind.id, url)
                rest = "/" + rest
                seen.add(rest)
                # the unprefixed form is still an SPA route (mail already sent uses it)
                assert UUID_PATH.fullmatch(rest) or f"'{rest}'" in routes, (kind.id, rest)
    assert {
        "/signup/confirm",
        "/signin",
        "/reset",
        "/email-change",
        "/account",
        "/unsubscribe",
    } <= seen
    assert any(UUID_PATH.fullmatch(s) for s in seen)


@pytest.mark.parametrize("kind", KINDS, ids=ids)
@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_the_logo_and_the_one_click_url_are_never_prefixed(kind: Kind, locale: str) -> None:
    message = sample_message(kind, locale)
    assert message.html is not None
    srcs = re.findall(r'src="([^"]+)"', message.html)
    assert srcs == [f"{BASE}/email/logo.png"]
    assert f"{BASE}/{locale}/email/" not in message.html + message.body
    if message.list_unsubscribe:
        assert message.list_unsubscribe == f"{BASE}/api/mail/unsubscribe?token=SAMPLE"
    assert f"{BASE}/{locale}/api" not in message.html + message.body


def test_the_sample_and_real_unsubscribe_links_split_page_and_one_click() -> None:
    settings = golden_settings()
    sample = sample_unsubscribe_links(settings, "da")
    assert sample.page == f"{BASE}/da/unsubscribe#token=SAMPLE&all=SAMPLE"
    assert sample.one_click == f"{BASE}/api/mail/unsubscribe?token=SAMPLE"
    assert sample_unsubscribe_links(settings).page.startswith(f"{BASE}/en/unsubscribe#")

    keys = Keys.from_secret(b"s" * 48)
    user = UUID("11111111-2222-3333-4444-555555555555")
    real = unsubscribe_links(settings, keys, user, "welcome", "sv")
    assert real.page.startswith(f"{BASE}/sv/unsubscribe#token=")
    assert "&all=" in real.page
    assert real.one_click.startswith(f"{BASE}/api/mail/unsubscribe?token=")
    assert "/sv/" not in real.one_click
    # an unknown language is English and is never echoed
    assert unsubscribe_links(settings, keys, user, "welcome", "xx/../").page.startswith(
        f"{BASE}/en/unsubscribe#"
    )


# every language the default settings offer, so a new one is checked without editing this list
@pytest.mark.parametrize("locale", locales.offered_locales(Settings(_env_file=None)))  # pyright: ignore[reportCallIssue]
async def test_a_sent_optional_mail_links_the_page_in_the_recipients_language(
    locale: str,
) -> None:
    async with SmtpFake() as smtp:
        s = Settings(
            smtp_host="127.0.0.1",
            smtp_port=smtp.port,
            smtp_tls="none",
            smtp_sender="classbeat@example.test",
            public_base_url=BASE,
        )
        prefs = MailPrefs(user_id=UUID("11111111-2222-3333-4444-555555555555"), locale=locale)
        keys = Keys.from_secret(b"s" * 48)
        assert await send(
            Mailer(s),
            s,
            "welcome",
            "to@example.com",
            REGISTRY["welcome"].sample,
            prefs=prefs,
            keys=keys,
        )
    [mail] = smtp.messages
    assert f"{BASE}/{locale}/unsubscribe#token=" in mail.text
    header = str(mail.message["List-Unsubscribe"]).strip("<>")
    assert header.startswith(f"{BASE}/api/mail/unsubscribe?token=")
    assert f'src="{BASE}/email/logo.png"' in mail.html


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/en/reset#token=abc-_9",
        f"{BASE}/sv/signup/confirm#token=abc",
        f"{BASE}/da",
        f"{BASE}/da/unsubscribe#token=a&all=b",
        f"{BASE}/api/mail/unsubscribe?token=abc",
        f"{BASE}/email/logo.png",
    ],
)
def test_the_url_check_accepts_the_prefixed_and_unprefixed_forms(url: str) -> None:
    assert check_link(url, base_url=BASE, support_address=SUPPORT) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/en/reset#token=abc",
        "https://classbeat.example.test.attacker.example/sv/reset#token=abc",
        "https://classbeat.example.test@attacker.example/en/reset",
        "https://user:pw@classbeat.example.test/en/reset",
        "http://classbeat.example.test/en/reset",
        "//attacker.example/en/reset",
        "/en/reset",
        "en/reset",
        "https://attacker.example/classbeat.example.test/en/reset",
        "https://classbeat.example.test:8443/en/reset",
    ],
)
def test_the_url_check_still_refuses_every_other_host_for_prefixed_paths(url: str) -> None:
    with pytest.raises(UnsafeLink):
        check_link(url, base_url=BASE, support_address=SUPPORT)
