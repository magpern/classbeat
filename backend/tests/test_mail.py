"""SMTP delivery and its settings (ADR 0003 section 5, "Mail delivery")."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from classbeat.config import Settings
from classbeat.mail import Mailer, MailError, MailMessage, send_logged
from smtpfake import SmtpFake


def settings(**values: object) -> Settings:
    return Settings(**values)  # pyright: ignore[reportArgumentType]


async def test_a_message_goes_through_smtp() -> None:
    async with SmtpFake() as smtp:
        mailer = Mailer(
            settings(
                smtp_host="127.0.0.1",
                smtp_port=smtp.port,
                smtp_tls="none",
                smtp_sender="classbeat@example.test",
            )
        )
        await mailer.send(MailMessage("to@example.com", "Hello", "Body line\n"))
    [received] = smtp.messages
    assert (received.to, received.subject) == ("to@example.com", "Hello")
    assert received.message["From"] == "classbeat@example.test"
    assert received.text.strip() == "Body line"


async def test_without_a_host_nothing_is_sent_and_it_says_so() -> None:
    with pytest.raises(MailError):
        await Mailer(settings()).send(MailMessage("to@example.com", "s", "b"))


async def test_a_refused_connection_is_a_mail_error_without_the_recipient() -> None:
    mailer = Mailer(settings(smtp_host="127.0.0.1", smtp_port=1, smtp_tls="none"))
    with pytest.raises(MailError) as excinfo:
        await mailer.send(MailMessage("secret.person@example.com", "s", "b"))
    assert "secret.person" not in str(excinfo.value)


async def test_a_failed_background_send_logs_the_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    mailer = Mailer(settings(smtp_host="127.0.0.1", smtp_port=1, smtp_tls="none"))
    await send_logged(
        mailer, MailMessage("secret.person@example.com", "Secret subject", "Secret body")
    )
    assert "mail not sent" in caplog.text
    for hidden in ("secret.person", "Secret subject", "Secret body"):
        assert hidden not in caplog.text


def test_a_message_never_shows_its_content_in_repr() -> None:
    assert "person@example.com" not in repr(MailMessage("person@example.com", "s", "b"))


def test_defaults_use_starttls_and_no_host() -> None:
    s = settings()
    assert (s.smtp_host, s.smtp_port, s.smtp_tls) == (None, 587, "starttls")
    assert s.signup_mode == "invite"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "mailpit", "::1", "[::1]"])
def test_plain_smtp_is_allowed_for_loopback_and_mailpit(host: str) -> None:
    assert settings(smtp_host=host, smtp_tls="none").smtp_tls == "none"


@pytest.mark.parametrize("host", ["smtp.example.com", "10.0.0.5", "mailpit.evil.example"])
def test_plain_smtp_is_refused_for_other_hosts(host: str) -> None:
    with pytest.raises(ValidationError):
        settings(smtp_host=host, smtp_tls="none")
    assert settings(smtp_host=host, smtp_tls="starttls").smtp_host == host
    assert settings(smtp_host=host, smtp_tls="tls").smtp_host == host


def test_the_smtp_password_comes_only_from_its_secret_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "smtp_password").write_text("mail-secret\n", encoding="utf-8")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("CLASSBEAT_SMTP_PASSWORD", "from-the-env")  # ignored
    loaded = Settings()
    assert loaded.smtp_password is not None
    assert loaded.smtp_password.get_secret_value() == "mail-secret"
    assert "mail-secret" not in repr(loaded)
    assert "mail-secret" not in str(loaded.model_dump())


def test_the_smtp_port_is_range_checked() -> None:
    with pytest.raises(ValidationError):
        settings(smtp_port=0)
