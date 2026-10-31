"""``GET /api/settings/public`` (ADR 0011 section 9): no sign-in, nothing but the banner and the
effective sign-up mode."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from classbeat.db import system_session
from classbeat.flags import effective

router = APIRouter(prefix="/api/settings", tags=["settings"])


class BannerOut(BaseModel):
    text: str  # plain text: the page renders it as text
    level: str


class PublicOut(BaseModel):
    banner: BannerOut | None
    signup_mode: str


@router.get("/public", response_model=PublicOut)
async def public_settings(request: Request, response: Response) -> PublicOut:
    response.headers["Cache-Control"] = "no-store"
    async with system_session(request.app.state.session_factory) as db:
        shown = await effective.banner(db)
        mode = await effective.signup_mode(db, request.app.state.settings.signup_mode)
    return PublicOut(
        banner=None if shown is None else BannerOut(text=shown.text, level=shown.level),
        signup_mode=mode,
    )
