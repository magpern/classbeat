"""The class API's error answers and id parsing, shared by ``classes.routes`` and
``classes.edit_routes``: a reason code only, never anything of a row."""

import re
from uuid import UUID

from fastapi.responses import JSONResponse

HASH = re.compile(r"^[0-9a-f]{64}$")


def refuse(reason: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def not_found() -> JSONResponse:
    return refuse("not_found", 404)


def internal() -> JSONResponse:
    """A stored row that no longer checks: the reason only, nothing of the row."""
    return JSONResponse({"detail": {"reason": "internal"}}, status_code=500)


def uuid_of(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None
