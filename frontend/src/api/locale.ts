import { request } from './client'
import { object, optionalText } from './decode'

// The account's language (ADR 0023; backend/src/classbeat/auth/locale_routes.py). `null` follows
// the browser.
export async function setAccountLocale(locale: string | null): Promise<string | null> {
  const body = object(await request('PUT', '/api/account/locale', { body: { locale } }), 'locale')
  return optionalText(body, 'locale')
}
