import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_LOCALE, setActiveLocale } from '../i18n/locales'
import { loadCatalogue, resetCatalogues } from '../i18n/t'
import { TITLE_MAX, copyTitle } from './classDuplicate'

// A made-up second locale, so the active locale can be switched to it (an unlisted code stays English).
vi.mock('../i18n/locales.json', () => ({ default: ['en', 'xx'] }))

afterEach(() => {
  setActiveLocale(DEFAULT_LOCALE)
  resetCatalogues()
})

describe('copyTitle', () => {
  it('cuts the title to the server limit with a longer wording than English', async () => {
    const wording = ' (en kopia av klassen, lite längre)'
    await loadCatalogue('xx', {
      xx: () => Promise.resolve({ default: { 'class.duplicate.copyOf': `{title}${wording}` } }),
    })
    setActiveLocale('xx')
    const out = copyTitle('x'.repeat(TITLE_MAX))
    expect(out.length).toBe(TITLE_MAX)
    expect(out.endsWith(wording)).toBe(true)
    expect(out).toBe('x'.repeat(TITLE_MAX - wording.length) + wording)
  })

  it('does not leave a space before the wording when the cut falls after one', () => {
    const out = copyTitle(`${'x'.repeat(92)} ${'y'.repeat(20)}`)
    expect(out.length).toBeLessThanOrEqual(TITLE_MAX)
    expect(out).toBe(`${'x'.repeat(92)} (copy)`)
  })
})
