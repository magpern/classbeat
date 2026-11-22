import type { PreviewFailure } from '../api/spotifyPreview'

// What a failure of the Spotify preview means to the person (ADR 0042 section 7). Pure: no React,
// no fetch. A reason code from the backend (or one made in the api module) maps to ONE sentence
// key in the catalogue and to what the page may do about it; a code this table does not know maps to
// the generic sentence, so a raw code or provider message is never shown.

export type FailureAction =
  /** Link to the Spotify connection (the S1 flow) to connect or re-consent. */
  | 'reconnect'
  /** Open a Spotify app, then press Play again. */
  | 'open_spotify'
  /** Wait; the sentence says for how long when Spotify said. */
  | 'wait'
  /** Sign in again. */
  | 'sign_in'
  /** Nothing to do now; try again in a moment. */
  | 'retry'
  /** Reload the class page: what the page shows is out of date. */
  | 'reload'
  | 'none'

export interface FailureInfo {
  /** The catalogue key of the sentence. */
  key: string
  action: FailureAction
  /** True: polling cannot get better until the person acts, so it stops. */
  stops: boolean
}

const TABLE: Readonly<Record<string, FailureInfo>> = {
  not_connected: { key: 'preview.err.not_connected', action: 'reconnect', stops: true },
  needs_reconnect: { key: 'preview.err.needs_reconnect', action: 'reconnect', stops: true },
  preview_not_granted: { key: 'preview.err.preview_not_granted', action: 'reconnect', stops: true },
  premium_or_allowlist: { key: 'preview.err.premium_or_allowlist', action: 'none', stops: true },
  no_device: { key: 'preview.err.no_device', action: 'open_spotify', stops: true },
  rate_limited: { key: 'preview.err.rate_limited', action: 'wait', stops: false },
  upstream_unavailable: { key: 'preview.err.upstream_unavailable', action: 'retry', stops: false },
  outbound_blocked: { key: 'preview.err.upstream_unavailable', action: 'retry', stops: false },
  invalid_response: { key: 'preview.err.invalid_response', action: 'retry', stops: false },
  network: { key: 'preview.err.network', action: 'retry', stops: false },
  unauthorized: { key: 'preview.err.unauthorized', action: 'sign_in', stops: true },
  no_draft: { key: 'preview.err.no_draft', action: 'reload', stops: true },
  not_found: { key: 'preview.err.not_found', action: 'none', stops: true },
  feature_off: { key: 'preview.err.not_found', action: 'none', stops: true },
  piece_out_of_range: { key: 'preview.err.out_of_range', action: 'none', stops: false },
  position_out_of_range: { key: 'preview.err.out_of_range', action: 'none', stops: false },
  stale_base: { key: 'preview.err.stale_base', action: 'reload', stops: false },
  not_ready: { key: 'preview.err.stale_base', action: 'reload', stops: false },
  no_analysis: { key: 'preview.err.no_analysis', action: 'none', stops: false },
  correction_limit: { key: 'preview.err.correction_limit', action: 'none', stops: false },
  invalid_input: { key: 'preview.err.invalid_input', action: 'none', stops: false },
}

const GENERIC: FailureInfo = { key: 'preview.err.generic', action: 'retry', stops: false }

/** The sentence and the way forward for a failure. */
export function describeFailure(failure: PreviewFailure): FailureInfo {
  const known = Object.hasOwn(TABLE, failure.reason) ? TABLE[failure.reason] : undefined
  if (known !== undefined) return known
  // Spotify's own 403 (Premium, or the app's allowlist) when no code came with it.
  if (failure.status === 403 && !failure.reason.startsWith('csrf')) return TABLE.premium_or_allowlist ?? GENERIC
  return GENERIC
}

/** Whole seconds a 429 asks to wait, at least 1 and at most an hour; null when Spotify did not say. */
export function retryWaitS(failure: PreviewFailure): number | null {
  if (failure.reason !== 'rate_limited' || failure.retryAfterS === null) return null
  return Math.min(3600, Math.max(1, Math.ceil(failure.retryAfterS)))
}

/** The sentence key for a rate limit that says when, else the one that does not. */
export function failureKey(failure: PreviewFailure): string {
  const info = describeFailure(failure)
  return info.key === 'preview.err.rate_limited' && retryWaitS(failure) !== null ? 'preview.err.rate_limited_for' : info.key
}
