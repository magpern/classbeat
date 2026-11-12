"""The SMTP checks that combine ``MailSettings`` fields or read the CA file (ADR 0003 section 5).

Moved from ``classbeat.config`` (#973, split on touch); ``Settings``' validators
``_plain_smtp_only_locally`` and ``_check_smtp_ca_file`` call them, in the same order. Not a field
group: ``check_ca_file`` reads the file to prove it is a PEM certificate.
"""

import ssl

from classbeat.config_hosts import _is_local_host
from classbeat.config_mail import MailSettings


def check_plain_smtp(settings: MailSettings) -> None:
    if (
        settings.smtp_tls == "none"
        and settings.smtp_host is not None
        and not _is_local_host(settings.smtp_host)
    ):
        raise ValueError("smtp_tls=none is only allowed for a loopback host or the mailpit service")


def check_ca_file(settings: MailSettings) -> None:
    path = settings.smtp_ca_file
    if path is None:
        return
    if settings.smtp_tls == "none":
        raise ValueError("smtp_ca_file cannot be combined with smtp_tls=none")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=str(path))
    except (OSError, ssl.SSLError):
        # The path and the OS error text stay out of the message.
        raise ValueError("smtp_ca_file must be a readable PEM certificate file") from None
