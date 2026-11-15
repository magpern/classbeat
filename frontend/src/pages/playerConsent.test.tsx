import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { needsDocumentLoad } from '../router/documentNavigation'
import { localize } from '../router/paths'
import { SIGNED_IN, fakeApi, reason } from '../test/fakeApi'
import { renderAt } from '../test/render'

// The consent step of the OpenID Connect provider for classbeat-player (ADR 0034 section 1).
const goToProvider = vi.hoisted(() => vi.fn())
vi.mock('../lib/navigation', () => ({ goToProvider }))

const R = '0b6f1c1e-6a3f-4c0e-9d8a-2f1d3c4b5a69'
const BACK = 'https://player.example/oidc/callback?code=abc&state=s1'

beforeEach(() => {
  goToProvider.mockReset()
})

describe('the player consent page', () => {
  it('allows and sends the browser to the address the server answers', async () => {
    const api = fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'POST /oidc/authorize/consent': { redirect: BACK },
    })
    renderAt(`/player-consent?r=${R}`)
    expect(await screen.findByText(/asks to sign you in as ann@example.test/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(BACK)
    })
    const [call] = api.to('POST', '/oidc/authorize/consent')
    expect(call?.body).toEqual({ r: R, allow: true })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(await screen.findByText('Returning to classbeat-player…')).toBeInTheDocument()
  })

  it('cancels with allow false', async () => {
    const denied = 'https://player.example/oidc/callback?error=access_denied&state=s1'
    const api = fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'POST /oidc/authorize/consent': { redirect: denied },
    })
    renderAt(`/player-consent?r=${R}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(denied)
    })
    expect(api.to('POST', '/oidc/authorize/consent')[0]?.body).toEqual({ r: R, allow: false })
  })

  it('shows the expired notice for request_expired', async () => {
    fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'POST /oidc/authorize/consent': reason(400, 'request_expired'),
    })
    renderAt(`/player-consent?r=${R}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }))
    expect(await screen.findByText(/has expired or was opened in another browser/)).toBeInTheDocument()
    expect(goToProvider).not.toHaveBeenCalled()
  })

  it('asks for the password when the request needs a re-authentication, then answers again', async () => {
    let confirmed = false
    const api = fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
      'POST /api/auth/reauth': () => {
        confirmed = true
        return { status: 'ok' }
      },
      'POST /oidc/authorize/consent': () =>
        confirmed ? { redirect: BACK } : reason(403, 'reauth_required'),
    })
    renderAt(`/player-consent?r=${R}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }))
    const dialog = await screen.findByRole('dialog', { name: 'Confirm it is you' })
    fireEvent.change(await within(dialog).findByLabelText('Password'), { target: { value: 'pw' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm with password' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(BACK)
    })
    expect(api.to('POST', '/oidc/authorize/consent')).toHaveLength(2)
  })

  it('without a well-formed request id shows the expired notice and asks nothing', async () => {
    const api = fakeApi({ 'GET /api/auth/session': SIGNED_IN })
    renderAt('/player-consent?r=not-an-id')
    expect(await screen.findByText(/has expired or was opened in another browser/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull()
    expect(api.to('POST', '/oidc/authorize/consent')).toHaveLength(0)
  })

  it('signed out, points to sign-in and back through the server\'s continue step', async () => {
    fakeApi({})
    renderAt(`/player-consent?r=${R}`)
    const links = await screen.findAllByRole('link', { name: 'Sign in' })
    const back = encodeURIComponent(`/oidc/authorize/continue?r=${R}`)
    expect(links.some((link) => link.getAttribute('href')?.includes(back))).toBe(true)
    expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull()
  })
})

describe('the server routes of the provider', () => {
  it('are never given a language prefix and are always a document load', () => {
    const target = `/oidc/authorize/continue?r=${R}`
    expect(localize(target, 'sv')).toBe(target)
    expect(needsDocumentLoad('/sv/signin', target, true)).toBe(true)
    expect(needsDocumentLoad('/sv/signin', '/oidc', false)).toBe(true)
    expect(needsDocumentLoad('/sv/signin', '/sv/oidcx', true)).toBe(false)
  })
})
