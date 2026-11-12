"""The environment's sign-up mode from its settings (ADR 0032 section 7, #976).

Pure: no database, no environment read. ``classbeat.flags.effective`` joins the result with the
stored ``signup_mode`` (stricter wins, ``classbeat.flags.core.stricter_signup``).

``CLASSBEAT_PUBLIC_SIGNUP`` is the one switch that opens sign-up to the public:

- ``signup_mode`` ``closed`` (the emergency brake) is ``closed`` whatever the flag says;
- the flag off is ``invite``: a ``signup_mode`` of ``open`` alone never opens (``Settings``
  refuses it at start-up, and this answers ``invite`` should it get past);
- the flag on is ``open``, except in prod while Turnstile is off (ADR 0025 section 5, the owner's
  human degrade): then ``invite``, so the degrade keeps sign-in working without leaving anonymous
  account creation without its captcha.
"""

from classbeat.flags.core import SignupMode


def environment_mode(
    *, signup_mode: SignupMode, public_signup: bool, turnstile_enabled: bool, environment: str
) -> SignupMode:
    """The sign-up mode the environment allows (before the database may tighten it)."""
    if signup_mode == "closed":
        return "closed"
    if not public_signup:
        return "invite"
    if environment == "prod" and not turnstile_enabled:
        return "invite"
    return "open"
