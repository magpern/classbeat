import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'

// What the page says for each Spotify error category (ADR 0009 section 8). Only the category
// comes from the server or the callback's query; a provider message is never shown.

// The sentences are the catalogue's `sentence.spotify.<category>` entries, the same codes the
// backend sends (ADR 0023 section 6); a category with no entry is `sentence.spotify.unknown`.
const KEYS: Record<string, CatalogueKey> = {
  invalid_grant: 'sentence.spotify.invalid_grant',
  app_misconfigured: 'sentence.spotify.app_misconfigured',
  not_allowlisted: 'sentence.spotify.not_allowlisted',
  forbidden: 'sentence.spotify.forbidden',
  rate_limited: 'sentence.spotify.rate_limited',
  quota_exhausted: 'sentence.spotify.quota_exhausted',
  upstream_unavailable: 'sentence.spotify.upstream_unavailable',
  not_found: 'sentence.spotify.not_found',
  scope_mismatch: 'sentence.spotify.scope_mismatch',
  invalid_response: 'sentence.spotify.invalid_response',
  account_changed: 'sentence.spotify.account_changed',
  access_denied: 'sentence.spotify.access_denied',
}

/** `retryAfter` is whole seconds from the callback's query; anything else counts as 60. */
export function spotifySentence(category: string, retryAfter: number | null = null): string {
  const seconds = retryAfter !== null && retryAfter > 0 ? retryAfter : 60
  if (category === 'rate_limited') {
    return t(KEYS.rate_limited!, { count: Math.max(1, Math.ceil(seconds / 60)) })
  }
  if (category === 'quota_exhausted') {
    return t(KEYS.quota_exhausted!, { count: Math.max(1, Math.ceil(seconds / 3600)) })
  }
  return t(Object.hasOwn(KEYS, category) ? KEYS[category]! : 'sentence.spotify.unknown')
}

/** Reads `?retry_after=` as a positive whole number of seconds, else null. */
export function parseRetryAfter(value: string | null): number | null {
  if (value === null || !/^[0-9]{1,9}$/.test(value)) return null
  const seconds = Number(value)
  return seconds > 0 ? seconds : null
}
