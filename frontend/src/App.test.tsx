import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from './test/fakeApi'
import { renderAt } from './test/render'

describe('App shell', () => {
  it('renders the product name in the header', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    renderAt('/')

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

    renderAt('/')

    const landing = screen.getByRole('region', { name: /classbeat picks the songs/i })
    expect(within(landing).getByText('Private beta')).toBeInTheDocument()
    expect(await within(screen.getByRole('banner')).findByText('API: ok')).toBeInTheDocument()
  })

  it('shows an error in the nav when signing out fails and keeps the user signed in', async () => {
    fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'POST /api/auth/logout': reason(500, 'boom'),
      'GET /api/classes': { classes: [] },
    })
    renderAt('/')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Sign out' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/went wrong on the server/)
    await userEvent.click(screen.getByRole('button', { name: 'ann@example.test' }))
    expect(await screen.findByRole('menuitem', { name: 'Sign out' })).not.toHaveAttribute('data-disabled')
    expect(window.location.pathname).toBe('/en')
  })
})
