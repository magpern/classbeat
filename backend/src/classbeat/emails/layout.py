"""The one layout: ``Content`` blocks to a text part and an HTML part (ADR 0022 section 2).

Table-based with inline styles, system fonts, a 600 px card, and a hosted logo whose ``alt`` text
carries the brand, so the mail reads with images off. No remote fonts, tracking pixel or script.
Every string is escaped here and nowhere else; every link passes ``check_link``.
"""

from dataclasses import dataclass
from html import escape
from typing import assert_never

from classbeat.emails import theme
from classbeat.emails.blocks import (
    Block,
    Bullets,
    Button,
    Content,
    EventTime,
    Heading,
    Note,
    Paragraph,
)
from classbeat.emails.catalogue import translator
from classbeat.emails.locales.en import S, format_when
from classbeat.emails.urls import check_link


@dataclass(frozen=True, slots=True)
class Branding:
    """What the admin may brand (ME2 fills it from settings; ME1 from the environment)."""

    sender_name: str
    support_address: str
    footer: str | None = None


@dataclass(frozen=True, slots=True)
class Ctx:
    """What a builder needs: the origin to link to, the branding and the catalogue."""

    base_url: str
    branding: Branding
    locale: str = "en"

    def url(self, path: str, token: str | None = None) -> str:
        link = f"{self.base_url}{path}" + (f"#token={token}" if token else "")
        return check_link(
            link, base_url=self.base_url, support_address=self.branding.support_address
        )

    def t(self, string_id: S, **params: object) -> str:
        return translator(self.locale)(string_id, **params)


def _e(value: str) -> str:
    return escape(value, quote=True)


def _link(ctx: Ctx, url: str) -> str:
    return check_link(url, base_url=ctx.base_url, support_address=ctx.branding.support_address)


def _mailto(ctx: Ctx) -> str:
    return f"mailto:{ctx.branding.support_address}"


def render_text(
    ctx: Ctx, content: Content, *, optional: bool = False, unsubscribe_url: str | None = None
) -> str:
    out: list[str] = []
    for block in content.blocks:
        match block:
            case Heading(text):
                out += [text, "=" * min(len(text), 60), ""]
            case Paragraph(text):
                out += [text, ""]
            case Button(label, url, _):
                out += [f"{label}:", _link(ctx, url), ""]
            case Note(text, contact):
                out += [f"{text} {ctx.branding.support_address}" if contact else text, ""]
            case Bullets(items):
                out += [*(f"- {item}" for item in items), ""]
            case EventTime(label, when):
                out += [f"{label}: {format_when(when)}", ""]
    out += ["--", content.reason, f"{ctx.t(S.FOOTER_SITE)}: {_link(ctx, ctx.base_url)}"]
    out.append(f"{ctx.t(S.FOOTER_SUPPORT)} {ctx.branding.support_address}")
    if ctx.branding.footer:
        out.append(ctx.branding.footer)
    if optional and unsubscribe_url:
        out.append(f"{ctx.t(S.FOOTER_UNSUBSCRIBE)}: {_link(ctx, unsubscribe_url)}")
    return "\n".join(out) + "\n"


_P = f"margin:0 0 16px 0;font-size:16px;line-height:24px;color:{theme.TEXT};"


def _html_block(ctx: Ctx, block: Block) -> str:
    match block:
        case Heading(text):
            return (
                f'<h1 style="margin:0 0 16px 0;font-size:24px;line-height:30px;'
                f'color:{theme.TEXT};">{_e(text)}</h1>'
            )
        case Paragraph(text):
            return f'<p style="{_P}">{_e(text)}</p>'
        case Button(label, url, True):
            return (
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                'style="margin:8px 0 24px 0;"><tr>'
                f'<td align="center" bgcolor="{theme.BRAND}" style="border-radius:6px;">'
                f'<a href="{_e(_link(ctx, url))}" style="display:inline-block;padding:12px 24px;'
                f"font-size:16px;font-weight:bold;color:{theme.BRAND_TEXT};"
                f'text-decoration:none;border-radius:6px;">{_e(label)}</a></td></tr></table>'
            )
        case Button(label, url, _):
            return (
                f'<p style="{_P}"><a href="{_e(_link(ctx, url))}" '
                f'style="color:{theme.BRAND};">{_e(label)}</a></p>'
            )
        case Note(text, contact):
            tail = ""
            if contact:
                address = ctx.branding.support_address
                tail = (
                    f' <a href="{_e(_mailto(ctx))}" style="color:{theme.BRAND};">{_e(address)}</a>'
                )
            return (
                f'<p style="margin:0 0 16px 0;padding:12px 16px;font-size:14px;line-height:21px;'
                f'background-color:{theme.NOTE_BG};color:{theme.TEXT};border-radius:6px;">'
                f"{_e(text)}{tail}</p>"
            )
        case Bullets(items):
            lis = "".join(f"<li>{_e(item)}</li>" for item in items)
            return f'<ul style="{_P}padding-left:20px;">{lis}</ul>'
        case EventTime(label, when):
            return (
                f'<p style="margin:0 0 16px 0;font-size:14px;line-height:21px;'
                f'color:{theme.MUTED};">{_e(label)}: <strong>{_e(format_when(when))}'
                "</strong></p>"
            )
        case _:
            assert_never(block)


def render_html(
    ctx: Ctx, content: Content, *, optional: bool = False, unsubscribe_url: str | None = None
) -> str:
    brand = ctx.branding
    logo = _link(ctx, f"{ctx.base_url}{theme.LOGO_PATH}")
    muted = f"font-size:13px;line-height:19px;color:{theme.MUTED};"
    body = "".join(_html_block(ctx, b) for b in content.blocks)
    footer = [
        f'<p style="margin:0 0 8px 0;{muted}">{_e(content.reason)}</p>',
        f'<p style="margin:0 0 8px 0;{muted}">'
        f'<a href="{_e(_link(ctx, ctx.base_url))}" style="color:{theme.MUTED};">'
        f"{_e(ctx.t(S.FOOTER_SITE))}</a> &middot; {_e(ctx.t(S.FOOTER_SUPPORT))} "
        f'<a href="{_e(_mailto(ctx))}" style="color:{theme.MUTED};">'
        f"{_e(brand.support_address)}</a></p>",
    ]
    if brand.footer:
        footer.append(f'<p style="margin:0 0 8px 0;{muted}">{_e(brand.footer)}</p>')
    if optional and unsubscribe_url:
        footer.append(
            f'<p style="margin:0;{muted}"><a href="{_e(_link(ctx, unsubscribe_url))}" '
            f'style="color:{theme.MUTED};">{_e(ctx.t(S.FOOTER_UNSUBSCRIBE))}</a></p>'
        )
    return (
        "<!doctype html>\n"
        f'<html lang="{_e(ctx.locale)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(content.subject)}</title></head>\n"
        f'<body style="margin:0;padding:0;background-color:{theme.PAGE_BG};'
        f'font-family:{theme.FONT};">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{_e(content.preheader)}</div>\n"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{theme.PAGE_BG}"><tr><td align="center" style="padding:24px 12px;">\n'
        f'<table role="presentation" width="{theme.CARD_WIDTH}" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:100%;max-width:{theme.CARD_WIDTH}px;'
        f"background-color:{theme.CARD_BG};border:1px solid {theme.BORDER};"
        'border-radius:8px;">\n'
        '<tr><td style="padding:24px 32px 8px 32px;">'
        f'<img src="{_e(logo)}" alt="{_e(ctx.t(S.FOOTER_SITE))}" width="{theme.LOGO_WIDTH}" '
        f'height="{theme.LOGO_HEIGHT}" style="display:block;border:0;'
        f'font-size:20px;font-weight:bold;color:{theme.BRAND};"></td></tr>\n'
        f'<tr><td style="padding:16px 32px 8px 32px;">{body}</td></tr>\n'
        f'<tr><td style="padding:16px 32px 24px 32px;border-top:1px solid {theme.BORDER};">'
        f"{''.join(footer)}</td></tr>\n"
        "</table></td></tr></table></body></html>\n"
    )
