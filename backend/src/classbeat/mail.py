"""Sending mail over SMTP (ADR 0003 section 5, "Mail delivery").

Settings choose the host, port, sender and TLS mode (``starttls``, ``tls`` or ``none``; ``none``
is refused for any host but loopback or the compose ``mailpit`` service, see ``Settings``). The
``smtp_ca_file`` (opt-in) swaps the system trust store for that one PEM file, with the same
verification and hostname check. The password comes from the ``smtp_password`` secret file.
``smtplib`` is blocking, so a send runs in a worker thread.

Mail is sent after the response (``send_logged`` is a background task). A failed send is logged
with the exception class only: never the recipient, the subject, the body or the exception text
(an SMTP server may echo the address back).
"""

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr

from classbeat.config import Settings
from classbeat.egress import refuse_in_worker, tls_client_context

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 15.0


class MailError(Exception):
    """A mail could not be delivered. The message never contains the recipient or the body."""


@dataclass(frozen=True, slots=True, repr=False)
class MailMessage:
    to: str
    subject: str
    body: str
    # The HTML alternative (``emails`` renders it from the same blocks as ``body``). Without it
    # the mail is single-part text/plain.
    html: str | None = None
    locale: str = "en"
    sender_name: str | None = None  # the From display name; the address is always smtp_sender
    reply_to: str | None = None
    # RFC 8058 one-click URL of an optional mail (``List-Unsubscribe`` and ``-Post`` headers).
    list_unsubscribe: str | None = None

    def __repr__(self) -> str:
        return "MailMessage(...)"


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._sender = settings.smtp_sender
        self._tls = settings.smtp_tls
        self._ca_file = str(settings.smtp_ca_file) if settings.smtp_ca_file else None
        self._username = settings.smtp_username
        self._password = (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )

    @property
    def configured(self) -> bool:
        return self._host is not None

    def _build(self, message: MailMessage) -> EmailMessage:
        built = EmailMessage()
        address = parseaddr(self._sender)[1] or self._sender
        if message.sender_name:
            username, _, domain = address.rpartition("@")
            built["From"] = Address(message.sender_name, username, domain)
        else:
            built["From"] = self._sender
        built["To"] = message.to
        if message.reply_to:
            built["Reply-To"] = message.reply_to
        built["Subject"] = message.subject
        built["Date"] = format_datetime(datetime.now(UTC))
        built["Message-ID"] = make_msgid(domain=address.rpartition("@")[2] or None)
        built["Auto-Submitted"] = "auto-generated"
        if message.list_unsubscribe:
            built["List-Unsubscribe"] = f"<{message.list_unsubscribe}>"
            built["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        built.set_content(message.body, charset="utf-8")
        if message.html is not None:
            built.add_alternative(message.html, subtype="html", charset="utf-8")
        # After the content: ``set_content`` clears every ``Content-*`` header.
        built["Content-Language"] = message.locale
        # The signed ``List-Unsubscribe`` URL has no space to fold at; with the default 78-column
        # limit the library would turn it into RFC 2047 encoded words, which a mail client does not
        # read as a URL. 998 is the RFC 5322 maximum (the body is already encoded by now).
        built.policy = built.policy.clone(max_line_length=998)
        return built

    def _send_blocking(self, message: MailMessage) -> None:
        if self._host is None:
            raise MailError("smtp_host is not configured")
        refuse_in_worker(self._host)
        built = self._build(message)
        context = tls_client_context(
            cafile=self._ca_file
        )  # verifying, and never a TLS key log (SSLKEYLOGFILE)
        if self._tls == "tls":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host, self._port, timeout=SMTP_TIMEOUT_SECONDS, context=context
            )
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT_SECONDS)
        with client:
            if self._tls == "starttls":
                client.starttls(context=context)
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(built)

    async def send(self, message: MailMessage) -> None:
        """Deliver one message; raises ``MailError`` (without details) when that fails."""
        if not self.configured:
            raise MailError("smtp_host is not configured")
        refuse_in_worker(self._host)  # web only: the worker never sends mail (ADR 0006 section 9)
        try:
            await asyncio.to_thread(self._send_blocking, message)
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            # The text of these can hold the recipient; only the class is kept.
            raise MailError(f"delivery failed ({type(exc).__name__})") from None


async def send_logged(mailer: Mailer, message: MailMessage) -> None:
    """A background task: send, and on failure log the error class only."""
    try:
        await mailer.send(message)
    except MailError as exc:
        logger.warning("mail not sent: %s", exc)
    except Exception as exc:  # a background task must never raise into the server
        logger.warning("mail not sent (%s)", type(exc).__name__)
