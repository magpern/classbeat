"""``auth_time`` and ``prompt=login`` (ADR 0034 section 1). Pure: no I/O.

- Without ``prompt=login``: the later of the session's sign-in time (``sessions.created_at``,
  which survives rotation) and its ``reauth_at``.
- With ``prompt=login``: a **new** re-authentication of this session, made after the request's
  ``received_at``. An older ``reauth_at``, even within the 10 minutes, does not count. A user with
  confirmed TOTP also needs a step-up after ``received_at``. A user who has no way to
  re-authenticate gets ``login_required``.

``decide`` answers the ``auth_time`` to put in the code, or what is missing: ``reauth_required``
and ``stepup_required`` are the 403 reasons the web app's guard already answers with a prompt
(and then retries); ``login_required`` is the OAuth error the client gets.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Missing = Literal["reauth_required", "stepup_required", "login_required"]


@dataclass(frozen=True, slots=True)
class SessionTimes:
    signed_in_at: datetime  # ``sessions.created_at``
    reauth_at: datetime | None
    stepup_at: datetime | None
    totp_enabled: bool


@dataclass(frozen=True, slots=True)
class Decision:
    auth_time: datetime | None
    missing: Missing | None


def _after(moment: datetime | None, start: datetime) -> bool:
    return moment is not None and moment > start


def decide(
    times: SessionTimes,
    *,
    prompt_login: bool,
    received_at: datetime,
    can_reauthenticate: bool,
) -> Decision:
    if not prompt_login:
        latest = max(times.signed_in_at, times.reauth_at or times.signed_in_at)
        return Decision(latest, None)
    if not _after(times.reauth_at, received_at):
        return Decision(None, "reauth_required" if can_reauthenticate else "login_required")
    if times.totp_enabled and not _after(times.stepup_at, received_at):
        return Decision(None, "stepup_required")
    return Decision(times.reauth_at, None)
