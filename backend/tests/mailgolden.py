"""Golden files of the mails (ME1, ML6): ``mail_golden/<kind>.txt`` and ``.html`` for English and
``mail_golden/<locale>/<kind>.txt`` and ``.html`` for every other catalogue.

Regenerate after an intended change to the layout or the catalogue, and read the diff:

    cd backend && uv run python tests/mailgolden.py
"""

from pathlib import Path

from classbeat.config import Settings
from classbeat.emails import REGISTRY, Kind, build
from classbeat.emails.catalogue import DEFAULT, LOCALES
from classbeat.emails.dispatch import sample_unsubscribe_links
from classbeat.mail import MailMessage

GOLDEN = Path(__file__).parent / "mail_golden"
RECIPIENT = "person@example.test"


def golden_settings() -> Settings:
    return Settings(public_base_url="https://classbeat.example.test")


def golden_dir(locale: str) -> Path:
    return GOLDEN if locale == DEFAULT else GOLDEN / locale


def sample_message(kind: Kind, locale: str = DEFAULT) -> MailMessage:
    settings = golden_settings()
    message = build(
        settings,
        kind,
        RECIPIENT,
        kind.sample,
        unsubscribe=sample_unsubscribe_links(settings),
        locale=locale,
    )
    assert message.html is not None
    return message


def main() -> None:
    for locale in LOCALES:
        folder = golden_dir(locale)
        folder.mkdir(exist_ok=True)
        for kind in REGISTRY.values():
            message = sample_message(kind, locale)
            assert message.html is not None
            (folder / f"{kind.id}.txt").write_text(message.body, encoding="utf-8", newline="\n")
            (folder / f"{kind.id}.html").write_text(message.html, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
