"""Golden files of the mails (ME1): ``mail_golden/<kind>.txt`` and ``.html``.

Regenerate after an intended change to the layout or the catalogue, and read the diff:

    cd backend && uv run python tests/mailgolden.py
"""

from pathlib import Path

from classbeat.config import Settings
from classbeat.emails import REGISTRY, Kind, build
from classbeat.emails.dispatch import sample_unsubscribe_links
from classbeat.mail import MailMessage

GOLDEN = Path(__file__).parent / "mail_golden"
RECIPIENT = "person@example.test"


def golden_settings() -> Settings:
    return Settings(public_base_url="https://classbeat.example.test")


def sample_message(kind: Kind) -> MailMessage:
    settings = golden_settings()
    message = build(
        settings, kind, RECIPIENT, kind.sample, unsubscribe=sample_unsubscribe_links(settings)
    )
    assert message.html is not None
    return message


def main() -> None:
    GOLDEN.mkdir(exist_ok=True)
    for kind in REGISTRY.values():
        message = sample_message(kind)
        assert message.html is not None
        (GOLDEN / f"{kind.id}.txt").write_text(message.body, encoding="utf-8", newline="\n")
        (GOLDEN / f"{kind.id}.html").write_text(message.html, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
