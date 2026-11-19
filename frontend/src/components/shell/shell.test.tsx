import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { PLAN_FREE, SIGNED_IN, fakeApi, USAGE_NONE } from '../../test/fakeApi'
import { JOB_ID } from '../../test/jobFixtures'
import { setWidth } from '../../test/layout'
import { renderAt } from '../../test/render'

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const TABS = ['Home', 'My classes', 'New class', 'Music', 'Account']

function stubApi(session: object = SIGNED_IN) {
  return fakeApi({
    'GET /api/auth/session': session,
    'GET /api/classes': { classes: [] },
    'GET /api/account/plan': { ...PLAN_FREE, classes_held: 2 },
  })
}

function mainNav() {
  return screen.getByRole('navigation', { name: 'Main' })
}

describe('navigation by width', () => {
  it('390 px: a bottom tab bar with the + in the middle, and the avatar top right', async () => {
    setWidth(390)
    stubApi()
    renderAt('/classes')
    await screen.findByRole('heading', { name: 'My classes' })
    const links = within(mainNav()).getAllByRole('link')
    expect(links.map((l) => l.textContent || l.getAttribute('aria-label'))).toEqual(TABS)
    expect(links[2]).toHaveAccessibleName('New class')
    expect(links[2]).toHaveAttribute('href', '/en/classes/new')
    expect(within(screen.getByRole('banner')).getByRole('button', { name: 'ann@example.test' })).toBeInTheDocument()
    expect(screen.queryByText('Classes this month')).toBeNull()
  })

  it.each([800, 1440])('%i px: a top bar with the logo, My classes, Music, New class and the avatar', async (width) => {
    setWidth(width)
    stubApi()
    renderAt('/classes')
    await screen.findByRole('heading', { name: 'My classes' })
    const bar = screen.getByRole('banner')
    expect(within(mainNav()).getAllByRole('link').map((l) => l.textContent)).toEqual(['My classes', 'Music'])
    expect(within(bar).getByRole('link', { name: 'classbeat, home' })).toHaveAttribute('href', '/en')
    expect(within(bar).getByRole('link', { name: 'New class' })).toHaveAttribute('href', '/en/classes/new')
    expect(within(bar).getByRole('button', { name: 'ann@example.test' })).toBeInTheDocument()
    // The meter left the shell (it lives on Account and My classes).
    expect(screen.queryByText('Classes this month')).toBeNull()
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it.each([800, 1440])('Account is one click away at %i px: the avatar menu', async (width) => {
    setWidth(width)
    stubApi()
    renderAt('/music')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Account' }))
    expect(window.location.pathname).toBe('/en/account')
  })

  it.each(['/', '/classes', '/music'])('Account is a tab at 390 px on %s', async (path) => {
    setWidth(390)
    stubApi()
    renderAt(path)
    await screen.findByRole('navigation', { name: 'Main' })
    expect(within(mainNav()).getByRole('link', { name: 'Account' })).toHaveAttribute('href', '/en/account')
  })

  it('marks the current page with aria-current, and My classes for a class page', async () => {
    setWidth(1440)
    stubApi()
    renderAt('/music')
    await screen.findByRole('heading', { name: 'Music' })
    expect(within(mainNav()).getByRole('link', { name: 'Music' })).toHaveAttribute('aria-current', 'page')
    expect(within(mainNav()).getByRole('link', { name: 'My classes' })).not.toHaveAttribute('aria-current')
  })
})

describe('keyboard', () => {
  it('puts a skip link first, then reaches every navigation item and the avatar menu with Tab', async () => {
    setWidth(1440)
    stubApi()
    renderAt('/music')
    await screen.findByRole('heading', { name: 'Music' })
    await userEvent.tab()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveFocus()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main-content')
    expect(document.getElementById('main-content')?.tagName).toBe('MAIN')
    expect(document.getElementById('main-content')).toHaveAttribute('tabindex', '-1')
    const reached: string[] = []
    for (let i = 0; i < 12 && reached.length < 5; i++) {
      await userEvent.tab()
      const active = document.activeElement
      const name = active?.getAttribute('aria-label') ?? active?.textContent ?? ''
      if (['classbeat, home', 'My classes', 'Music', 'New class', 'ann@example.test'].includes(name)) {
        reached.push(name)
      }
    }
    expect(reached).toEqual(['classbeat, home', 'My classes', 'Music', 'New class', 'ann@example.test'])
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('menuitem', { name: 'Account' })).toBeInTheDocument()
  })
})

describe('avatar menu and Admin', () => {
  it.each([390, 800, 1440])('at %i px the menu opens below the avatar', async (width) => {
    setWidth(width)
    stubApi()
    renderAt('/classes')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    expect(await screen.findByRole('menu')).toHaveAttribute('data-side', 'bottom')
  })

  it('has Account and Sign out, and no Admin for a normal user', async () => {
    setWidth(390)
    stubApi()
    renderAt('/classes')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    expect(await screen.findByRole('menuitem', { name: 'Account' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Admin' })).toBeNull()
  })

  it('shows Admin under the avatar for an admin, and it opens /admin in the shell', async () => {
    setWidth(1440)
    fakeApi({
      'GET /api/auth/session': ADMIN,
      'GET /api/usage': USAGE_NONE,
      'GET /api/admin/whoami': { email: 'ann@example.test' },
    })
    renderAt('/music')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Admin' }))
    expect(window.location.pathname).toBe('/admin')
    expect(within(mainNav()).getByRole('link', { name: 'Music' })).toBeInTheDocument()
  })

  it('shows Admin to an admin whose TOTP check is missing or lapsed (role is still user)', async () => {
    setWidth(1440)
    fakeApi({
      'GET /api/auth/session': { ...SIGNED_IN, session: { ...SIGNED_IN.session, admin_pending_mfa: true } },
      'GET /api/usage': USAGE_NONE,
    })
    renderAt('/account')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    expect(await screen.findByRole('menuitem', { name: 'Admin' })).toBeInTheDocument()
  })
})

describe('focus layout', () => {
  it('New class hides the navigation and shows Close', async () => {
    setWidth(1440)
    fakeApi({ 'GET /api/auth/session': SIGNED_IN, 'GET /api/connections': { connections: [] } })
    renderAt('/classes/new')
    await screen.findByRole('heading', { name: 'Design a class' })
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Close' })).toHaveAttribute('href', '/en')
    expect(screen.getByRole('link', { name: 'classbeat, home' })).toBeInTheDocument()
  })

  it('Designing hides the navigation and shows Back to home', async () => {
    setWidth(390)
    fakeApi({ 'GET /api/auth/session': SIGNED_IN })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('link', { name: 'Back to home' })).toHaveAttribute('href', '/en')
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
  })
})

describe('signed out', () => {
  it('keeps the plain header with Sign in and Sign up and no main navigation', async () => {
    setWidth(1440)
    fakeApi({})
    renderAt('/')
    const nav = await screen.findByRole('navigation', { name: 'Account' })
    expect(within(nav).getByRole('link', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
  })
})
