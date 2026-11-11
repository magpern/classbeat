"""Refusals of the billing path (ADR 0031 sections 3 and 6). Pure: no I/O."""

from typing import Final

# The ``billing_log.action`` codes (migration 0046): one per user billing action.
ACTIONS: Final = ("checkout", "change", "prices")


class BillingRefused(Exception):
    """A billing action refused for a reason the caller may see (409 ``quote_changed``, 403
    ``billing_closed``, ...). ``billing_session`` records it as ``outcome = 'refused'`` and
    re-raises it; a fast-forward turns it into an ``AdminRefused``."""

    def __init__(self, reason: str, status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
