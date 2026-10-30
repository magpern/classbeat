// What the page says for each Spotify error category (ADR 0009 section 8). Only the category
// comes from the server or the callback's query; a provider message is never shown.

const UNKNOWN = 'Something went wrong with Spotify. Try again later.'
const SCOPE = 'Spotify did not give the access classbeat needs. Connect again and allow it.'

const SENTENCES: Record<string, string> = {
  invalid_grant: 'Spotify no longer accepts the connection. Reconnect Spotify.',
  app_misconfigured:
    'Spotify does not recognise this app setup. Check the Client ID and the redirect URI in your Spotify app.',
  not_allowlisted:
    "Spotify refused this account. In your Spotify app's User Management add this account's name and email (Development Mode allows 5 people), and make sure the app's owner has Spotify Premium.",
  forbidden: 'Spotify refused this request for your account or playlist.',
  upstream_unavailable: 'Spotify is not answering.',
  not_found: 'Spotify could not find that playlist.',
  scope_mismatch: SCOPE,
  invalid_response: SCOPE,
  account_changed:
    'This is a different Spotify account than before. Confirm to switch (classbeat forgets the working playlist).',
  access_denied: 'Spotify was not connected.',
}

function plural(n: number, unit: string): string {
  return n === 1 ? `1 ${unit}` : `${String(n)} ${unit}s`
}

/** `retryAfter` is whole seconds from the callback's query; anything else counts as 60. */
export function spotifySentence(category: string, retryAfter: number | null = null): string {
  const seconds = retryAfter !== null && retryAfter > 0 ? retryAfter : 60
  if (category === 'rate_limited') {
    const minutes = Math.max(1, Math.ceil(seconds / 60))
    return `Spotify asks to slow down; trying again in about ${plural(minutes, 'minute')}.`
  }
  if (category === 'quota_exhausted') {
    const hours = Math.max(1, Math.ceil(seconds / 3600))
    return `Your Spotify app has used up its allowance for now. Try again in about ${plural(hours, 'hour')}.`
  }
  return Object.hasOwn(SENTENCES, category) ? (SENTENCES[category] ?? UNKNOWN) : UNKNOWN
}

/** Reads `?retry_after=` as a positive whole number of seconds, else null. */
export function parseRetryAfter(value: string | null): number | null {
  if (value === null || !/^[0-9]{1,9}$/.test(value)) return null
  const seconds = Number(value)
  return seconds > 0 ? seconds : null
}
