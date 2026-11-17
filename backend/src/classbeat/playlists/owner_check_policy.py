"""Why the owner check of ADR 0008 section 2 refused a playlist, as flags and value kinds only
(#1153). It never changes the check (``common.owned``); it only describes a refusal for one log
line, so a refused write says which condition failed.

Nothing returned holds a value Spotify answered or the account: the id and owner become flags
(``id_ok``, ``owner_ok``) and a form (``owner_form``), the two privacy fields their kind
(``"true"``, ``"false"``, ``"null"``, ``"missing"`` or ``"other"``). The keys avoid the log's
masked names (``public`` and ``state`` are masked by ``classbeat.log``), so they reach the line.
"""

from collections.abc import Mapping
from typing import Any, Final, cast

MISSING: Final = "missing"


def value_kind(found: Mapping[str, Any], key: str) -> str:
    """The kind of ``found[key]``: ``true``, ``false``, ``null``, ``missing`` or ``other``."""
    if key not in found:
        return MISSING
    value = found[key]
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "other"


def owner_form(owner: object, account: str | None) -> str:
    """How the answered owner id compares with the connected account, without either value:
    ``same``, ``case`` (equal ignoring case), ``other`` (another string), ``null``, ``not_string``,
    or ``no_account`` when no account is stored."""
    if account is None:
        return "no_account"
    if owner is None:
        return "null"
    if not isinstance(owner, str):
        return "not_string"
    if owner == account:
        return "same"
    if owner.casefold() == account.casefold():
        return "case"
    return "other"


def refusal(
    found: object, playlist_id: str, account: str | None, purpose: str
) -> dict[str, object]:
    """The log fields of a refused owner check of ``purpose`` (``pool`` or ``final``)."""
    if not isinstance(found, Mapping):
        return {"purpose": purpose, "answer": "not_mapping", "account_ok": account is not None}
    playlist = cast(Mapping[str, Any], found)
    owner: object = playlist.get("owner_id")
    return {
        "purpose": purpose,
        "answer": "mapping",
        "account_ok": account is not None,
        "id_ok": playlist.get("id") == playlist_id,
        "owner_ok": account is not None and owner == account,
        "owner_form": MISSING if "owner_id" not in playlist else owner_form(owner, account),
        "public_kind": value_kind(playlist, "public"),
        "collaborative_kind": value_kind(playlist, "collaborative"),
    }
