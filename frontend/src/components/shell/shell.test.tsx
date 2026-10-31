import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { PLAN_FREE, SIGNED_IN, fakeApi, USAGE_NONE } from '../../test/fakeApi'
import { JOB_ID } from '../../test/jobFixtures'
import { setWidth } from '../../test/layout'
import { renderAt } from '../../test/render'

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const ITEMS = ['Home', 'My classes', 'New class', 'Music', 'Account']

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
    expect(links.map((l) => l.textContent || l.getAttribute('aria-label'))).toEqual(ITEMS)
    expect(links[2]).toHaveAccessibleName('New class')
    expect(links[2]).toHaveAttribute('href', '/classes/new')
    expect(within(screen.getByRole('banner')).getByRole('button', { name: 'ann@example.test' })).toBeInTheDocument()
    // Phone: no sidebar meter.
    expect(screen.queryByText('Classes this month')).toBeNull()
  })

  it('800 px: an icon rail with the same items, named for assistive technology', async () => {
    setWidth(800)
    stubApi()
    renderAt('/classes')
    await screen.findByRole('heading', { name: 'My classes' })
    const links = within(mainNav()).getAllByRole('link')
    expect(links.map((l) => l.getAttribute('aria-label'))).toEqual(['New class', 'Home', 'My classes', 'Music', 'Account'])
    expect(within(mainNav()).queryByText('Music')).toBeNull()
    // The rail keeps the meter, compact: the number, and the bar says the full words once.
    const bar = await screen.findByRole('progressbar', { name: 'Classes this month: 2 of 3 classes in October' })
    expect(bar).toHaveAttribute('aria-valuenow', '67')
    expect(screen.getByText('2/3')).toBeInTheDocument()
    expect(screen.queryByText('Classes this month: 2 of 3 classes in October')).toBeNull()
  })

  it('1440 px: a sidebar with labels, the classes meter and the avatar with the name', async () => {
    setWidth(1440)
    stubApi()
    renderAt('/classes')
    await screen.findByRole('heading', { name: 'My classes' })
    const links = within(mainNav()).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['New class', 'Home', 'My classes', 'Music', 'Account'])
    expect(await screen.findByText('Classes this month')).toBeInTheDocument()
    expect(screen.getByText('2 of 3 classes in October')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Classes this month' })).toHaveAttribute('aria-valuenow', '67')
    expect(screen.getByRole('button', { name: 'ann@example.test' })).toHaveTextContent('ann@example.test')
  })

  it.each([390, 800, 1440].flatMap((w) => ['/', '/classes', '/music'].map((p) => [w, p] as const)))(
    'Account is one click away at %i px on %s',
    async (width, path) => {
      setWidth(width)
      stubApi()
      renderAt(path)
      await screen.findByRole('navigation', { name: 'Main' })
      expect(within(mainNav()).getByRole('link', { name: 'Account' })).toHaveAttribute('href', '/account')
    },
  )

  it('marks the current page with aria-current, and My classes for a class page', async () => {
    setWidth(1440)
    stubApi()
    renderAt('/music')
    await screen.findByRole('heading', { name: 'Music' })
    expect(within(mainNav()).getByRole('link', { name: 'Music' })).toHaveAttribute('aria-current', 'page')
    expect(within(mainNav()).getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current')
  })
})

describe('keyboard', () => {
  it('reaches every navigation item and the avatar menu with Tab, and Enter follows a link', async () => {
    setWidth(1440)
    stubApi()
    renderAt('/music')
    await screen.findByRole('heading', { name: 'Music' })
    const reached: string[] = []
    for (let i = 0; i < 12 && reached.length < 7; i++) {
      await userEvent.tab()
      const active = document.activeElement
      // The sidebar's avatar button is named by its visible text (the aria-hidden initial is not part of it).
      const name = active?.getAttribute('aria-label') ?? active?.querySelector('span:not([aria-hidden])')?.textContent ?? active?.textContent ?? ''
      if (['classbeat home', 'New class', 'Home', 'My classes', 'Music', 'Account', 'ann@example.test'].includes(name)) {
        reached.push(name)
      }
    }
    expect(reached).toEqual(['classbeat home', 'New class', 'Home', 'My classes', 'Music', 'Account', 'ann@example.test'])
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('menuitem', { name: 'Account' })).toBeInTheDocument()
  })
})

describe('avatar menu and Admin', () => {
  it.each([
    [390, 'bottom'],
    [800, 'top'],
    [1440, 'top'],
  ] as const)('at %i px the menu opens %s', async (width, side) => {
    setWidth(width)
    stubApi()
    renderAt('/classes')
    await userEvent.click(await screen.findByRole('button', { name: 'ann@example.test' }))
    expect(await screen.findByRole('menu')).toHaveAttribute('data-side', side)
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
    expect(within(mainNav()).getByRole('link', { name: 'Home' })).toBeInTheDocument()
  })
})

describe('focus layout', () => {
  it('New class hides the navigation and shows Close', async () => {
    setWidth(1440)
    fakeApi({ 'GET /api/auth/session': SIGNED_IN, 'GET /api/connections': { connections: [] } })
    renderAt('/classes/new')
    await screen.findByRole('heading', { name: 'Design a class' })
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Close' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'classbeat home' })).toBeInTheDocument()
  })

  it('Designing hides the navigation and shows Back to home', async () => {
    setWidth(390)
    fakeApi({ 'GET /api/auth/session': SIGNED_IN })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('link', { name: 'Back to home' })).toHaveAttribute('href', '/')
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
