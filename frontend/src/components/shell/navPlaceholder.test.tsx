import { screen, within } from '@testing-library/react'
import { renderToString } from 'react-dom/server'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { App } from '../../App'
import { I18nProvider } from '../../i18n/I18n'
import { loadCatalogue, resetCatalogues } from '../../i18n/t'
import { fakeApi } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// Takeover gate (PP5): the signed-out nav arrives after the session does, and used to push the
// page down. The prerender and the loading state keep its room instead: nothing visible and no link.

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

  it('shows no sign-in or sign-up link, and no visible text, in that room', () => {
    expect(header).not.toMatch(/href="[^"]*(signin|signup)/i)
    const placeholder = /<div aria-hidden="true" data-testid="nav-placeholder"[\s\S]*?<\/div>/.exec(header)?.[0] ?? ''
    expect(placeholder).not.toContain('<a')
    // Any text sits in a span that is not rendered (it only gives the button its width).
    const visible = placeholder.replace(/<span class="invisible[^>]*>[^<]*<\/span>/g, '').replace(/<[^>]*>/g, '').trim()
    expect(visible).toBe('')
  })

  beforeAll(async () => {
    await loadCatalogue('sv')
    await loadCatalogue('da')
  })
  afterAll(() => {
    resetCatalogues()
  })

  it.each([
    ['en', 'Sign in', 'Sign up'],
    ['sv', 'Logga in', 'Registrera dig'],
    ['da', 'Log ind', 'Opret dig'],
  ] as const)('holds the room for the buttons\' own labels in %s', (lang, signIn, signUp) => {
    const page = renderToString(
      <I18nProvider initial={lang}>
        <App initialLocation={{ pathname: `/${lang}`, search: '' }} />
      </I18nProvider>,
    )
    const placeholder = /<div aria-hidden="true" data-testid="nav-placeholder"[\s\S]*?<\/div>/.exec(page)?.[0] ?? ''
    expect(placeholder).toContain(`>${signIn}</span>`)
    expect(placeholder).toContain(`>${signUp}</span>`)
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
