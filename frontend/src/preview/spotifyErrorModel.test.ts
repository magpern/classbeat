import { describe, expect, it } from 'vitest'
import da from '../i18n/da'
import { en } from '../i18n/en'
import sv from '../i18n/sv'
import { describeFailure, failureKey, retryWaitS } from './spotifyErrorModel'

const failure = (reason: string, status = 409, retryAfterS: number | null = null) => ({ reason, status, retryAfterS })

// Every reason the backend gives for the preview (preview_spotify.py, spotify_corrections.py) and the ones the api module makes.
const KNOWN = [
  'not_connected', 'needs_reconnect', 'preview_not_granted', 'premium_or_allowlist', 'no_device', 'rate_limited',
  'upstream_unavailable', 'outbound_blocked', 'invalid_response', 'network', 'unauthorized', 'no_draft', 'not_found',
  'feature_off', 'piece_out_of_range', 'position_out_of_range', 'stale_base', 'not_ready', 'no_analysis',
  'correction_limit', 'invalid_input',
]

describe('describeFailure', () => {
  it.each(KNOWN)('%s has a sentence in English, Swedish and Danish', (reasonCode) => {
    const info = describeFailure(failure(reasonCode))
    for (const key of [info.key, failureKey(failure(reasonCode, 429, 5))]) {
      expect(en).toHaveProperty([key])
      expect(sv).toHaveProperty([key])
      expect(da).toHaveProperty([key])
    }
  })

  it('an unknown reason is the generic sentence: no raw code is shown', () => {
    for (const reasonCode of ['something_new', '', 'DROP TABLE', 'constructor', 'toString']) {
      expect(describeFailure(failure(reasonCode)).key).toBe('preview.err.generic')
    }
    expect(en['preview.err.generic']).not.toMatch(/\{/)
  })

  it('a bare 403 is Spotify refusing (Premium or the allowlist); a CSRF 403 is not', () => {
    expect(describeFailure(failure('unknown', 403)).key).toBe('preview.err.premium_or_allowlist')
    expect(describeFailure(failure('csrf_token_invalid', 403)).key).toBe('preview.err.generic')
  })

  it('says what to do: reconnect, open Spotify, wait or sign in', () => {
    expect(describeFailure(failure('preview_not_granted')).action).toBe('reconnect')
    expect(describeFailure(failure('needs_reconnect')).action).toBe('reconnect')
    expect(describeFailure(failure('no_device')).action).toBe('open_spotify')
    expect(describeFailure(failure('rate_limited', 429)).action).toBe('wait')
    expect(describeFailure(failure('unauthorized', 401)).action).toBe('sign_in')
  })

  it('stops polling only for what needs the person', () => {
    expect(describeFailure(failure('needs_reconnect')).stops).toBe(true)
    expect(describeFailure(failure('premium_or_allowlist')).stops).toBe(true)
    expect(describeFailure(failure('rate_limited', 429)).stops).toBe(false)
    expect(describeFailure(failure('upstream_unavailable', 503)).stops).toBe(false)
  })
})

describe('a rate limit says when to try again', () => {
  it('rounds up to whole seconds and bounds it', () => {
    expect(retryWaitS(failure('rate_limited', 429, 7.2))).toBe(8)
    expect(retryWaitS(failure('rate_limited', 429, 0.1))).toBe(1)
    expect(retryWaitS(failure('rate_limited', 429, 99_999))).toBe(3600)
  })

  it('uses the sentence with the time only when Spotify said it', () => {
    expect(failureKey(failure('rate_limited', 429, 7))).toBe('preview.err.rate_limited_for')
    expect(failureKey(failure('rate_limited', 429))).toBe('preview.err.rate_limited')
    expect(en['preview.err.rate_limited_for']).toContain('{seconds}')
  })

  it('is not a wait for any other failure', () => {
    expect(retryWaitS(failure('upstream_unavailable', 503, 5))).toBeNull()
  })
})
