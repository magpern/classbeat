import { afterEach, describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'
import { loadCatalogue, resetCatalogues } from './t'
import { setActiveLocale } from './locales'
import { ApiError } from '../api/client'
import { swedebeatError } from '../api/swedebeatMatch'

// ADR 0028 section 2 for the reasons the Swedebeat match routes refuse with (`swmatch.reason.*`, worded by
// `swedebeatError`): an error is at most 20 words (what happened and what to do), "classbeat" is never the
// subject, and en, sv and da each have every key.

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

const KEYS = (Object.keys(en) as CatalogueKey[]).filter((k) => k.startsWith('swmatch.reason.'))
const catalogues = (): [string, Catalogue][] => [['en', en], ['sv', sv], ['da', da]]

const textOf = (catalogue: Catalogue, key: CatalogueKey): string => {
  const message = catalogue[key]
  if (typeof message !== 'string') throw new Error(`${key} is missing or not a plain string`)
  return message
}

/** Every reason code the routes can send, as the table in `api/swedebeatMatch.ts` knows them. */
const CODES = [
  'feature_off', 'not_found', 'stale_match', 'handoff_expired', 'class_busy', 'not_connected', 'rate_limited',
  'upstream_unavailable', 'key_unavailable', 'outbound_blocked', 'invalid_input', 'not_checked', 'busy', 'quota',
  'analysis_limit', 'too_large', 'idempotency_mismatch', 'decrypt_failed', 'pool_busy', 'song_picking_off',
  'consent_missing', 'revision_limit', 'chat_full', 'stale_base', 'swedebeat_rounds', 'write_unresolved',
] as const

afterEach(() => {
  setActiveLocale('en')
  resetCatalogues()
})

describe('Swedebeat match reasons', () => {
  it('is not checked against an empty set of keys', () => {
    expect(KEYS.length).toBe(23)
    expect(KEYS).toContain('swmatch.reason.fallback')
    expect(words('one {two} three')).toBe(3)
  })

  it('has every key in all three languages', () => {
    for (const [lang, catalogue] of [['sv', sv], ['da', da]] as const) {
      expect(KEYS.filter((k) => typeof catalogue[k] !== 'string'), lang).toEqual([])
    }
  })

  it('keeps every reason within 20 words in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of KEYS) {
        expect(words(textOf(catalogue, key)), `${lang} ${key}`).toBeLessThanOrEqual(20)
      }
    }
  })

  it('never makes the product name the subject of a sentence', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of KEYS) expect(textOf(catalogue, key), `${lang} ${key}`).not.toMatch(/classbeat/i)
    }
  })

  it('words every code from the catalogue, never a code or a provider message', () => {
    const known = new Set(KEYS.map((k) => textOf(en, k)))
    for (const code of CODES) {
      const text = swedebeatError(new ApiError(409, code, null, null))
      expect(known.has(text), code).toBe(true)
      expect(text, code).not.toContain(code)
    }
    expect(swedebeatError(new ApiError(429, null, null, null))).toBe(en['swmatch.reason.rate_limited'])
    expect(swedebeatError(new ApiError(500, 'something_new', null, null))).toBe(en['swmatch.reason.fallback'])
    expect(swedebeatError(new Error('boom'))).toBe(en['swmatch.reason.fallback'])
  })

  it('words the reasons in the active language', async () => {
    await loadCatalogue('sv')
    setActiveLocale('sv')
    expect(swedebeatError(new ApiError(409, 'class_busy', null, null))).toBe(sv['swmatch.reason.class_busy'])
  })
})
