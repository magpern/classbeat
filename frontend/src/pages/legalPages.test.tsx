import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { en } from '../i18n/en'
import { legalEn } from '../i18n/legal.en'
import { legalDa } from '../i18n/legal.da'
import { legalSv } from '../i18n/legal.sv'
import { fakeApi, SIGNED_IN } from '../test/fakeApi'
import { renderAt } from '../test/render'
import { legalKeys } from './legal/structure'

describe('legal pages without a session', () => {
  it('shows the privacy notice signed out, with the marked placeholder box', async () => {
    fakeApi({})
    renderAt('/privacy')
    expect(await screen.findByRole('heading', { level: 1, name: 'Privacy notice' })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'support@classbeat.eu' })[0]).toHaveAttribute(
      'href',
      'mailto:support@classbeat.eu',
    )
    const box = screen.getByRole('complementary', { name: 'Who is responsible' })
    expect(within(box).getAllByText('[To be filled in by the owner]')).toHaveLength(4)
    // The five cookies are all named.
    for (const name of ['session', 'device', 'csrf', 'oauth', 'pending'])
      expect(screen.getByText(new RegExp(`^classbeat_${name}:`))).toBeInTheDocument()
  })

  it('shows the terms signed out and links back to the privacy notice', async () => {
    fakeApi({})
    renderAt('/terms/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Terms of use' })).toBeInTheDocument()
    const links = screen.getAllByRole('link', { name: 'privacy notice' })
    expect(links[0]).toHaveAttribute('href', '/privacy')
  })

  it('links both pages from the footer, signed out and signed in', async () => {
    fakeApi({})
    const out = renderAt('/signin')
    const footer = await screen.findByRole('navigation', { name: 'Legal' })
    expect(within(footer).getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', '/privacy')
    expect(within(footer).getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/terms')
    out.unmount()
    fakeApi({ 'GET /api/auth/session': SIGNED_IN })
    renderAt('/privacy')
    expect(await screen.findByRole('navigation', { name: 'Legal' })).toBeInTheDocument()
  })

  it('links both pages from the sign-in page', async () => {
    fakeApi({})
    renderAt('/signin')
    await screen.findByRole('heading', { level: 1, name: 'Sign in' })
    expect(screen.getAllByRole('link', { name: 'privacy notice' })[0]).toHaveAttribute('href', '/privacy')
    expect(screen.getAllByRole('link', { name: 'terms of use' })[0]).toHaveAttribute('href', '/terms')
  })
})

describe('legal catalogue keys', () => {
  const needed = [...legalKeys('privacy'), ...legalKeys('terms')]
  const fixed = Object.keys(legalEn)

  it.each([
    ['en', legalEn as Record<string, unknown>],
    ['sv', legalSv as Record<string, unknown>],
    ['da', legalDa as Record<string, unknown>],
  ])('%s has every key the pages use, and nothing else', (_code, catalogue) => {
    const own = Object.keys(catalogue).sort()
    expect(own).toEqual([...Object.keys(legalEn)].sort())
    for (const key of [...needed, ...fixed]) expect(typeof catalogue[key]).toBe('string')
  })

  it('English is the catalogue the pages read', () => {
    for (const key of needed) expect(Object.hasOwn(en, key)).toBe(true)
  })

  it('names the same cookies and the same placeholders in every language', () => {
    for (const catalogue of [legalSv, legalDa] as Record<string, string>[])
      for (const key of needed) {
        const slots = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort().join()
        expect(slots(catalogue[key] ?? '')).toBe(slots((legalEn as Record<string, string>)[key] ?? ''))
      }
    for (const catalogue of [legalSv, legalDa, legalEn] as Record<string, string>[])
      for (const cookie of ['session', 'device', 'csrf', 'oauth', 'pending'])
        expect(
          catalogue[
            `privacy.cookies.li${['session', 'device', 'csrf', 'oauth', 'pending'].indexOf(cookie) + 1}`
          ],
        ).toMatch(new RegExp(`^classbeat_${cookie}:`))
  })
})
