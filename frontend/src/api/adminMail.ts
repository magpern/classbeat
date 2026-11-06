import { del, get, request } from './client'
import { decodeFlag, type Flag } from './adminSettings'
import { flag, list, object, text } from './decode'

// The admin Mail page (ADR 0022 sections 2 and 6; backend/src/classbeat/admin/mail.py, ME2 #653).
// The four mail settings are written through the settings routes; the preview is an iframe on
// `previewUrl`, never fetched here. Every write needs a step-up.

const BASE = '/api/admin'

export type MailKey = 'mail_sender_name' | 'mail_support_email' | 'mail_footer' | 'mail_kinds_disabled'

export interface MailKind {
  id: string
  category: string
  /** An essential kind is always sent and has no switch. */
  essential: boolean
  userCanOptOut: boolean
  adminCanDisable: boolean
  enabled: boolean
}

export interface MailView {
  settings: Flag[]
  kinds: MailKind[]
}

function decodeKind(value: unknown): MailKind {
  const item = object(value, 'kind')
  return {
    id: text(item, 'id'),
    category: text(item, 'category'),
    essential: flag(item, 'essential'),
    userCanOptOut: flag(item, 'user_can_opt_out'),
    adminCanDisable: flag(item, 'admin_can_disable'),
    enabled: flag(item, 'enabled'),
  }
}

export async function getMail(signal?: AbortSignal): Promise<MailView> {
  const body = object(await get(`${BASE}/mail`, signal), 'mail')
  return { settings: list(body, 'settings').map(decodeFlag), kinds: list(body, 'kinds').map(decodeKind) }
}

function settingPath(key: MailKey): string {
  return `${BASE}/settings/${encodeURIComponent(key)}`
}

export async function setMailSetting(key: MailKey, value: string | string[]): Promise<Flag> {
  return decodeFlag(await request('PUT', settingPath(key), { body: { value } }))
}

export async function clearMailSetting(key: MailKey): Promise<Flag> {
  return decodeFlag(await del(settingPath(key)))
}

/** The path the sandboxed preview iframe loads (`part` is `html` or `text`). */
export function previewUrl(kind: string, part: 'html' | 'text'): string {
  return `${BASE}/mail/${encodeURIComponent(kind)}/preview?part=${part}`
}

/** Sends the kind's sample to the admin's own address (10 an hour). */
export async function sendTest(kind: string): Promise<void> {
  await request('POST', `${BASE}/mail/${encodeURIComponent(kind)}/test`, {})
}
