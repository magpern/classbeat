import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { translate } from '../i18n/t'
import { EXAMPLES, exampleKey } from '../publicSite/examples'
import { SIGNED_IN, fakeApi, json } from '../test/fakeApi'
import { SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { renderAt } from '../test/render'
import { connectionsBody } from '../test/songFixtures'

// PP6 (#895): "Create a class like this" hands an example's slug to the designer. The API is faked.

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../lib/navigation', () => ({ goToProvider }))

const SLUG = 'hill-challenge'
const PROMPT = translate('en', exampleKey(SLUG, 'prompt'))
const KEY = 'classbeat.example'
const READY = {
  ...SESSION_ROUTES,
  'GET /api/connections': connectionsBody(false),
  'GET /api/music/mowl-playlists': { playlists: [] },
  'POST /api/jobs': () => json(jobBody('queued', { kind: 'interpret_request' }), 201),
}
const ANON = { authenticated: false, csrf_token: 'a' }
const prompt = () => screen.findByLabelText('Describe your class')
const card = async () => within((await screen.findByText('This page is for signed-in users.')).parentElement as HTMLElement)
const writes = (api: ReturnType<typeof fakeApi>) => api.calls.filter((c) => c.method !== 'GET')

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  window.sessionStorage.clear()
  goToProvider.mockClear()
})

describe('the call to action on an example page', () => {
  it.each(EXAMPLES.map((e) => e.slug))('%s links to the designer with its slug and no text', (slug) => {
    fakeApi({})
    renderAt(`/examples/${slug}`)
    const link = screen.getByRole('link', { name: 'Create a class like this' })
    expect(link).toHaveAttribute('href', `/en/classes/new?example=${slug}`)
  })
})

describe('the start screen from an example link', () => {
  it('fills the prompt with the example page text, focuses it and writes nothing before submit', async () => {
    const api = fakeApi(READY)
    renderAt(`/classes/new?example=${SLUG}`)
    const field = await prompt()
    expect(field).toHaveValue(PROMPT)
    expect(field).toHaveFocus()
    // The same words as the example page shows.
    cleanup()
    renderAt(`/examples/${SLUG}`)
    expect(screen.getByTestId('example-prompt').textContent).toBe(PROMPT)
    // No job, class, credit or MOWL write: only reads.
    expect(writes(api)).toEqual([])
    expect(api.to('POST', '/api/jobs')).toEqual([])
  })

  it('sends the job only when the instructor submits', async () => {
    const api = fakeApi(READY)
    renderAt(`/classes/new?example=${SLUG}`)
    await prompt()
    expect(writes(api)).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect((api.to('POST', '/api/jobs')[0]?.body as { input: { request: string } }).input.request).toBe(PROMPT)
  })

  it('removes the kept slug when it has used it', async () => {
    fakeApi(READY)
    renderAt(`/classes/new?example=${SLUG}`)
    await prompt()
    await waitFor(() => {
      expect(window.localStorage.getItem(KEY)).toBeNull()
    })
  })

  it('ignores an unknown slug and any other query value', async () => {
    fakeApi(READY)
    renderAt('/classes/new?example=nope&prompt=Write+something+else')
    expect(await prompt()).toHaveValue('')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('ignores a kept slug older than 24 hours', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ slug: SLUG, at: Date.now() - 25 * 3600 * 1000 }))
    fakeApi(READY)
    renderAt('/classes/new')
    expect(await prompt()).toHaveValue('')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })
})

describe('the example survives what the link cannot carry', () => {
  it('sign-in: the card offers sign-in and Join the beta, both with only the validated slug in return_to', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    renderAt(`/classes/new?example=${SLUG}&prompt=evil&return_to=//evil.example`)
    const want = `return_to=${encodeURIComponent(`/classes/new?example=${SLUG}`)}`
    const inCard = await card()
    expect(inCard.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', `/en/signin?${want}`)
    expect(inCard.getByRole('link', { name: 'Join the beta' })).toHaveAttribute('href', `/en/signup?${want}`)
    expect(window.localStorage.getItem(KEY)).toContain(SLUG)
  })

  it('an unknown slug leaves return_to as the path alone and keeps nothing', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    renderAt('/classes/new?example=nope')
    expect((await card()).getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/en/signin?return_to=%2Fclasses%2Fnew')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('sign-in: after signing in the user lands on the designer with the prompt filled', async () => {
    let signedIn = false
    const api = fakeApi({
      ...READY,
      'GET /api/auth/session': () => (signedIn ? SIGNED_IN : ANON),
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/login': () => {
        signedIn = true
        return { id: 'u1', email: 'ann@example.test', role: 'user' }
      },
    })
    renderAt(`/signin?return_to=${encodeURIComponent(`/classes/new?example=${SLUG}`)}`)
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ann@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct horse battery' } })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await prompt()).toHaveValue(PROMPT)
    expect(window.location.pathname + window.location.search).toBe(`/en/classes/new?example=${SLUG}`)
    expect(api.to('POST', '/api/jobs')).toEqual([])
  })

  it('OAuth: the provider start carries the slug in return_to, and the return opens the filled designer', async () => {
    const api = fakeApi({
      ...READY,
      'GET /api/auth/session': ANON,
      'GET /api/auth/oauth/providers': { providers: ['google'] },
      'POST /api/auth/oauth/google/start': { authorization_url: 'https://idp.example/a' },
    })
    renderAt(`/signin?return_to=${encodeURIComponent(`/classes/new?example=${SLUG}`)}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Continue with Google' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalled()
    })
    const sent = api.to('POST', '/api/auth/oauth/google/start')[0]?.body as { return_to: string }
    expect(sent.return_to).toBe(`/classes/new?example=${SLUG}`)
    // The backend redirects there once signed in.
    cleanup()
    fakeApi(READY)
    renderAt(sent.return_to)
    expect(await prompt()).toHaveValue(PROMPT)
  })

  it('email sign-up: the kept slug fills the prompt after the confirmation link and a plain sign-in', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    renderAt(`/classes/new?example=${SLUG}`)
    await card()
    cleanup()
    // Later, signed in, on /classes/new without the query.
    const api = fakeApi(READY)
    renderAt('/classes/new')
    expect(await prompt()).toHaveValue(PROMPT)
    expect(writes(api)).toEqual([])
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('missing MOWL connection: the slug is kept through the connect step and used afterwards', async () => {
    fakeApi({ ...READY, 'GET /api/connections': { connections: [] } })
    renderAt(`/classes/new?example=${SLUG}`)
    expect(await screen.findByText(/connect/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Describe your class')).toBeNull()
    expect(window.localStorage.getItem(KEY)).toContain(SLUG)
    cleanup()
    // Back from the account page, MOWL now connected.
    const api = fakeApi(READY)
    renderAt('/classes/new')
    expect(await prompt()).toHaveValue(PROMPT)
    expect(writes(api)).toEqual([])
  })
})

describe('the privacy page', () => {
  it('lists the kept example among the browser storage', async () => {
    fakeApi({})
    renderAt('/privacy')
    expect(await screen.findByText(/classbeat\.example/)).toHaveTextContent('up to 24 hours')
  })
})
