import { screen, within } from '@testing-library/react'
import untranslated from '../i18n/untranslated.json'
import { describe, expect, it } from 'vitest'
import { en } from '../i18n/en'
import { helpDa } from '../i18n/help.da'
import { helpEn } from '../i18n/help.en'
import { helpSv } from '../i18n/help.sv'
import { fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'
import { helpKeys } from './help/structure'

describe('help page', () => {
  it('renders signed out with the configured support address', async () => {
    fakeApi({
      'GET /api/settings/public': {
        banner: null,
        signup_mode: 'invite',
        support_address: 'help@example.org',
      },
    })
    renderAt('/help')
    expect(await screen.findByRole('heading', { level: 1, name: 'Help and contact' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Contact' })).toBeInTheDocument()
    const link = await screen.findByRole('link', { name: 'help@example.org' })
    expect(link).toHaveAttribute('href', 'mailto:help@example.org')
    expect(document.body.textContent).not.toContain('support@classbeat.eu')
    expect(screen.getByText(/never send a password/i)).toBeInTheDocument()
  })

  it('says so when no address is configured, without a broken link', async () => {
    fakeApi({ 'GET /api/settings/public': { banner: null, signup_mode: 'invite' } })
    renderAt('/help')
    expect(await screen.findByText(en['help.contact.none'])).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /@/ })).toBeNull()
  })

  it('says so when the settings request fails', async () => {
    fakeApi({ 'GET /api/settings/public': json({}, 500) })
    renderAt('/help')
    expect(await screen.findByText(en['help.contact.none'])).toBeInTheDocument()
  })

  it('is linked from the footer', async () => {
    fakeApi({})
    renderAt('/signin')
    const footer = await screen.findByRole('navigation', { name: 'Legal' })
    expect(within(footer).getByRole('link', { name: 'Help' })).toHaveAttribute('href', '/en/help')
  })
})

describe('help catalogue keys', () => {
  const slots = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort().join()
  it.each([
    ['en', helpEn],
    ['sv', helpSv],
    ['da', helpDa],
  ] as [string, Record<string, string>][])(
    '%s has every key the page uses and nothing else',
    (lang, catalogue) => {
      // A key on untranslated.json shows the English text until PP7 (ADR 0023 section 8).
      const translated = Object.keys(helpEn).filter((key) => lang === 'en' || !untranslated.includes(key))
      for (const key of helpKeys().filter((k) => translated.includes(k))) expect(typeof catalogue[key]).toBe('string')
      expect(Object.keys(catalogue).sort()).toEqual(translated.sort())
    },
  )

  it('keeps the same placeholders in every language', () => {
    for (const catalogue of [helpSv, helpDa] as Record<string, string>[])
      for (const [key, value] of Object.entries(helpEn))
        if (catalogue[key] !== undefined) expect(slots(catalogue[key])).toBe(slots(value))
  })
})
