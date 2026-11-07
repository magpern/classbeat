"""The admin area's request body limit (#227 follow-up, #303 and #315 reviews): ``AdminBodyLimit``
refuses an ``/api/admin`` body over ``MAX_BODY_BYTES`` with 413 ``body_too_large``, and
``body_refusal`` tells that refusal and FastAPI's own unreadable-body 400 apart from any other
400 or 413. ``classbeat.admin.routes.install`` installs the middleware and turns both refusals
into the gate first, then the refusal with its ``admin.request`` row.
"""

from typing import Any

from fastapi import HTTPException

# The largest admin request body (#227 follow-up). The largest valid body today is a code's
# create (an 80-character label and a few short fields), well under 4 KiB; 64 KiB leaves room for
# the settings API (a 280-character banner) without letting a body be large enough to matter.
MAX_BODY_BYTES = 64 * 1024
BODY_TOO_LARGE = "body_too_large"
BODY_UNREADABLE = "body_unreadable"
# The two body refusals by exactly what raises them, so a 400 or 413 a route raises itself is
# never taken for one (#315 review): FastAPI's own body-parse failure
# (``fastapi.routing.get_request_handler``) and ``AdminBodyLimit``'s.
_FASTAPI_BODY_ERROR = "There was an error parsing the body"
_BODY_REFUSALS: dict[tuple[int, object], str] = {
    (400, _FASTAPI_BODY_ERROR): BODY_UNREADABLE,
    (413, repr({"reason": BODY_TOO_LARGE})): BODY_TOO_LARGE,
}


def body_refusal(exc: object) -> str | None:
    status, detail = getattr(exc, "status_code", None), getattr(exc, "detail", None)
    key = detail if isinstance(detail, str) else repr(detail)
    return _BODY_REFUSALS.get((status, key)) if isinstance(status, int) else None


class AdminBodyLimit:
    """ASGI middleware: an ``/api/admin`` request body over ``MAX_BODY_BYTES``, by its
    ``Content-Length`` or as it streams in (a chunked body has none), is refused 413 when the
    route reads it. The refusal is raised from ``receive``, so FastAPI's body parsing re-raises it
    before any dependency, and the admin area's handler runs the gate first. A route that reads
    no body never sees it."""

    def __init__(self, app: Any, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/api/admin"):
            await self.app(scope, receive, send)
            return
        declared = next(
            (v for k, v in scope.get("headers", []) if k.lower() == b"content-length"), None
        )
        too_large = declared is not None and (
            not declared.isdigit() or int(declared) > self.max_bytes
        )
        seen = 0

        async def limited() -> Any:
            nonlocal seen
            if too_large:
                raise HTTPException(status_code=413, detail={"reason": BODY_TOO_LARGE})
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise HTTPException(status_code=413, detail={"reason": BODY_TOO_LARGE})
            return message

        await self.app(scope, limited, send)
