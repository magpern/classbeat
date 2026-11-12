import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { DEFAULT_LOCALE, setActiveLocale } from '../i18n/locales'
import { loadCatalogue, resetCatalogues } from '../i18n/t'
import { ApiError } from './client'
import { errorMessage, oauthOutcomeMessage } from './messages'

// #522: the reauth_stale sentence by what is known about the account's password.
describe('oauthOutcomeMessage for a failed re-authentication', () => {
  const first = 'Nothing changed: the provider did not confirm a sign-in in the last 5 minutes.'

  it('shows only what failed while the password status is still loading', () => {
    expect(oauthOutcomeMessage('reauth_stale')).toBe(first)
    expect(oauthOutcomeMessage('reauth_stale', undefined)).toBe(first)
  })

  it('offers the password when the account has one', () => {
    expect(oauthOutcomeMessage('reauth_stale', true)).toBe(`${first} Use your password instead.`)
  })

  it('explains how to set a password when it has none', () => {
    const text = oauthOutcomeMessage('reauth_stale', false)
    expect(text.startsWith('Nothing changed: the sign-in was not recent.')).toBe(true)
    expect(text).toContain('“Forgot your password?”')
    expect(text).not.toContain('Use your password instead')
  })

  it('gives both ways when the status could not be read', () => {
    const text = oauthOutcomeMessage('reauth_stale', null)
    expect(text).toContain('Use your password, or set one')
    expect(text).toContain('“Forgot your password?”')
  })

  it('leaves the other outcomes as they were', () => {
    expect(oauthOutcomeMessage('denied')).toBe('The sign-in was cancelled at the provider.')
    expect(oauthOutcomeMessage('denied', true)).toBe('The sign-in was cancelled at the provider.')
  })
})

describe('oauthOutcomeMessage for a new provider account on the sign-in screen (#617)', () => {
  const tail = 'Create an account to join the invite-only beta.'

  it('names the provider account that has no classbeat account', () => {
    expect(oauthOutcomeMessage('no_account', undefined, 'google')).toBe(
      `No classbeat account is linked to this Google account. ${tail}`,
    )
    expect(oauthOutcomeMessage('no_account', undefined, 'github')).toBe(
      `No classbeat account is linked to this GitHub account. ${tail}`,
    )
    expect(oauthOutcomeMessage('no_account', undefined, 'microsoft')).toBe(
      `No classbeat account is linked to this Microsoft account. ${tail}`,
    )
  })

  it('never shows an unknown or missing provider name', () => {
    for (const provider of [null, undefined, '<b>evil</b>', 'toString']) {
      expect(oauthOutcomeMessage('no_account', undefined, provider)).toBe(
        `No classbeat account is linked to this provider account. ${tail}`,
      )
    }
  })

  it('says nothing about an invite code or a field below', () => {
    const text = oauthOutcomeMessage('no_account', undefined, 'google')
    expect(text).not.toMatch(/invite code|below/i)
    expect(text).not.toBe(oauthOutcomeMessage('invite_required'))
  })
})

// #1004: a 503 "busy" (rate-limiter lock timeout, Retry-After) uses the calm, translated busy text, not the server-fault one.
describe('errorMessage for a busy service', () => {
  const busy = 'The server is busy. Try again in a few seconds.'
  const server = 'Something went wrong on the server. Try again in a moment.'

  it('words 503 with reason busy', () => {
    expect(errorMessage(new ApiError(503, 'busy', 2, null))).toBe(busy)
    expect(errorMessage(new ApiError(503, 'busy', null, null))).toBe(busy)
  })

  it('words a 503 with Retry-After and no worded reason', () => {
    expect(errorMessage(new ApiError(503, null, 2, null))).toBe(busy)
    expect(errorMessage(new ApiError(503, 'something_new', 2, null))).toBe(busy)
  })

  describe('in sv', () => {
    beforeAll(async () => {
      await loadCatalogue('sv')
      setActiveLocale('sv')
    })
    afterAll(() => {
      setActiveLocale(DEFAULT_LOCALE)
      resetCatalogues()
    })

    it('uses the translated text', () => {
      const text = 'Servern är upptagen. Försök igen om några sekunder.'
      expect(errorMessage(new ApiError(503, 'busy', 2, null))).toBe(text)
      expect(errorMessage(new ApiError(503, null, 2, null))).toBe(text)
    })
  })

  it('keeps a worded reason and the generic server message otherwise', () => {
    expect(errorMessage(new ApiError(503, 'passkey_busy', 2, null))).toContain('Passkey sign-in is busy')
    expect(errorMessage(new ApiError(503, null, null, null))).toBe(server)
    expect(errorMessage(new ApiError(500, null, 2, null))).toBe(server)
  })
})
