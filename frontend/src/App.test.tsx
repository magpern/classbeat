import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { SIGNED_IN, fakeApi, reason } from './test/fakeApi'

describe('App shell', () => {
  it('renders the product name in the header', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    render(<App />)

    expect(within(screen.getByRole('banner')).getByText('classbeat')).toBeInTheDocument()
  })

  it('renders the landing section and the API status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ status: 'ok' }), {
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      ),
    )

    render(<App />)

    const landing = screen.getByRole('region', { name: /classbeat picks the songs/i })
    expect(within(landing).getByText('Private beta')).toBeInTheDocument()
    expect(await within(screen.getByRole('banner')).findByText('API: ok')).toBeInTheDocument()
  })

  it('shows an error in the nav when signing out fails and keeps the user signed in', async () => {
    fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'POST /api/auth/logout': reason(500, 'boom'),
    })
    window.history.replaceState(null, '', '/')
    render(<App />)
    const nav = await screen.findByRole('navigation', { name: 'Account' })
    fireEvent.click(within(nav).getByRole('button', { name: 'Sign out' }))
    await waitFor(() => {
      expect(within(nav).getByRole('alert')).toHaveTextContent(/went wrong on the server/)
    })
    expect(within(nav).getByRole('button', { name: 'Sign out' })).toBeEnabled()
    expect(window.location.pathname).toBe('/')
  })
})
