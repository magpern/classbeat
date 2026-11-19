import { screen, within } from '@testing-library/react'
import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { fakeApi } from '../../test/fakeApi'
import { renderAt } from '../../test/render'
import { App } from '../../App'

// Issue 1272: on a phone the signed-out header is two rows at most in every language (brand and language,
// then Sign in and Sign up). jsdom has no layout, so this keeps the shape the browser check
// (e2e/tests/signed-out-header-phone.spec.ts, which counts the rows in sv at 360 px) depends on.

const PHONE = 'max-[35rem]:'

describe('the signed-out header on a phone', () => {
  it('lets the picker and the buttons join the brand row, and gives the buttons a row of their own', async () => {
    fakeApi({ 'GET /api/auth/session': { authenticated: false, csrf_token: 'x', locales: ['en', 'sv', 'da'] } })
    renderAt('/')
    const banner = screen.getByRole('banner')
    const nav = await within(banner).findByRole('navigation', { name: 'Account' })
    const picker = within(banner).getByLabelText('Language')
    const group = nav.parentElement as HTMLElement
    // the picker is a neighbour of the nav inside a group that has no box of its own on a phone
    expect(group).toContainElement(picker)
    expect(nav).not.toContainElement(picker)
    expect(group.className).toContain(`${PHONE}contents`)
    // the row spreads brand and picker, and the buttons' nav takes the full row
    expect(group.parentElement?.className).toContain(`${PHONE}justify-between`)
    expect(nav.className).toContain(`${PHONE}w-full`)
    // Sign in and Sign up stay in the nav
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Sign in', 'Sign up'])
  })

  it('keeps the account nav and the hidden room in step, so the page does not move when the nav arrives', () => {
    const html = renderToString(
      <I18nProvider initial="en">
        <App initialLocation={{ pathname: '/en', search: '' }} />
      </I18nProvider>,
    )
    const header = /<header[\s\S]*?<\/header>/.exec(html)?.[0] ?? ''
    const room = /<div aria-hidden="true" data-testid="nav-placeholder" class="([^"]*)"/.exec(header)?.[1] ?? ''
    expect(room).toContain(`${PHONE}w-full`)
    // the group around it is the same one the real controls sit in
    expect(header).toContain(`ml-auto flex flex-wrap items-center justify-end gap-2 ${PHONE}contents`)
  })
})
