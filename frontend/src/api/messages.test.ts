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
