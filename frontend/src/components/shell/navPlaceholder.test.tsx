import { screen, within } from '@testing-library/react'
import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { App } from '../../App'
import { I18nProvider } from '../../i18n/I18n'
import { fakeApi } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// Takeover gate (PP5): the signed-out nav arrives after the session does, and used to push the
// page down. The prerender and the loading state keep its room instead, with no text and no link.

describe('the signed-out header while the session loads', () => {
  const html = renderToString(
    <I18nProvider initial="en">
      <App initialLocation={{ pathname: '/en', search: '' }} />
    </I18nProvider>,
  )
  const header = /<header[\s\S]*?<\/header>/.exec(html)?.[0] ?? ''

  it('keeps the nav\'s room in the prerender', () => {
    expect(header).toContain('data-testid="nav-placeholder"')
    expect(header).toContain('aria-hidden="true"')
  })

  it('shows no sign-in or sign-up link, and no text, in that room', () => {
    expect(header).not.toMatch(/signin|signup|Sign in|Sign up/i)
    const placeholder = /<div aria-hidden="true" data-testid="nav-placeholder"[\s\S]*?<\/div>/.exec(header)?.[0] ?? ''
    expect(placeholder.replace(/<[^>]*>/g, '').trim()).toBe('')
    expect(placeholder).not.toContain('<a')
  })
})

describe('the signed-out header once the session is known', () => {
  it('swaps the room for the real nav', async () => {
    fakeApi({})
    renderAt('/')
    const banner = screen.getByRole('banner')
    expect(within(banner).getByTestId('nav-placeholder')).toBeInTheDocument()
    expect(await within(banner).findByRole('link', { name: 'Sign in' })).toBeInTheDocument()
    expect(within(banner).queryByTestId('nav-placeholder')).toBeNull()
  })
})
