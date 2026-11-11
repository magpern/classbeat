import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from './test/fakeApi'
import { recallExample, rememberExample } from './lib/exampleHandoff'
import { renderAt } from './test/render'

describe('App shell', () => {
  it('renders the product name in the header', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    renderAt('/')

    expect(within(screen.getByRole('banner')).getByText('classbeat')).toBeInTheDocument()
  })

  it('renders the landing section and no API status pill', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    renderAt('/')

    const landing = await screen.findByRole('region', { name: /timed to the music/i })
    expect(within(landing).getByText('Private beta')).toBeInTheDocument()
    expect(screen.queryByText(/^API:/)).not.toBeInTheDocument()
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

  it('forgets a kept example slug when signing out, and keeps it when signing out fails', async () => {
    const routes = { 'GET /api/auth/session': SIGNED_IN, 'GET /api/classes': { classes: [] } }
    fakeApi({ ...routes, 'POST /api/auth/logout': reason(500, 'boom') })
    rememberExample('hill-challenge')
    renderAt('/')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Sign out' }))
    await screen.findByRole('alert')
    expect(recallExample()).toBe('hill-challenge')

    fakeApi({ ...routes, 'POST /api/auth/logout': {} })
    await userEvent.click(screen.getByRole('button', { name: 'ann@example.test' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Sign out' }))
    await vi.waitFor(() => {
      expect(recallExample()).toBeNull()
    })
  })
})
