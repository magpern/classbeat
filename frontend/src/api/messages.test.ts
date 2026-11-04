import { describe, expect, it } from 'vitest'
import { oauthOutcomeMessage } from './messages'

// #522: the reauth_stale sentence by what is known about the account's password.
describe('oauthOutcomeMessage for a failed re-authentication', () => {
  const first =
    'The provider did not confirm that you signed in again in the last 5 minutes, so nothing was changed.'

  it('shows only what failed while the password status is still loading', () => {
    expect(oauthOutcomeMessage('reauth_stale')).toBe(first)
    expect(oauthOutcomeMessage('reauth_stale', undefined)).toBe(first)
  })

  it('offers the password when the account has one', () => {
    expect(oauthOutcomeMessage('reauth_stale', true)).toBe(`${first} Use your password instead.`)
  })

  it('explains how to set a password when it has none', () => {
    const text = oauthOutcomeMessage('reauth_stale', false)
    expect(text.startsWith(first)).toBe(true)
    expect(text).toContain('"Forgot your password?"')
    expect(text).not.toContain('Use your password instead')
  })

  it('gives both ways when the status could not be read', () => {
    const text = oauthOutcomeMessage('reauth_stale', null)
    expect(text).toContain('Use your password instead, if you have one.')
    expect(text).toContain('"Forgot your password?"')
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
