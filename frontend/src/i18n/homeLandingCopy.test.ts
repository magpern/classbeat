import { describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'


// ADR 0028 section 2 for Home and Landing: button 1 to 3 words, label at most 4, helper at most 12,
// error at most 20; "classbeat" is never the subject of an app-screen sentence (Home); in en, sv, da.

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

const LIMITS: readonly [CatalogueKey, number][] = [
  ['landing.signUp', 3],
  ['home.design.button', 3],
  ['home.seeAll', 3],
  ['home.quick', 4],
  ['home.recent', 4],
  ['setup.title', 4],
  ['setup.connectNamed', 4],
  ['landing.example.title', 4],
  ['landing.how.title', 4],
  ['landing.lead', 12],
  ['landing.example.note', 12],
  ['landing.how.describe.text', 12],
  ['landing.how.approve.text', 12],
  ['landing.how.save.text', 12],
  ['home.loadFailed', 20],
  ['home.empty', 20],
]

const HOME_KEYS = (Object.keys(en) as CatalogueKey[]).filter((k) => k.startsWith('home.') || k.startsWith('setup.'))

/** The text of an entry; sv and da may omit a key (then English shows), and none of these is a plural. */
const text = (catalogue: Catalogue, key: CatalogueKey): string => {
  const message = catalogue[key] ?? en[key]
  return typeof message === 'string' ? message : ''
}

const catalogues = (): [string, Catalogue][] => [['en', en], ['sv', sv], ['da', da]]

describe('Home and Landing copy', () => {
  it('stays within the ADR 0028 length limits in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const [key, limit] of LIMITS) {
        expect(words(text(catalogue, key)), `${lang} ${key}`).toBeLessThanOrEqual(limit)
      }
    }
  })

  it('has one short landing title that is not repeated by the lead', () => {
    for (const [lang, catalogue] of catalogues()) {
      expect(words(text(catalogue, 'landing.title')), lang).toBeLessThanOrEqual(8)
      expect(text(catalogue, 'landing.lead'), lang).not.toContain(text(catalogue, 'landing.title'))
    }
  })

  it('does not use the product name on the signed-in Home', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of HOME_KEYS) {
        expect(text(catalogue, key), `${lang} ${key}`).not.toMatch(/classbeat/i)
      }
    }
  })
})
