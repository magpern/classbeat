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

/** Every reason code the routes can send, with the catalogue key its text must come from. */
const CODES: Readonly<Record<string, CatalogueKey>> = {
  feature_off: 'swmatch.reason.feature_off',
  not_found: 'swmatch.reason.not_found',
  stale_match: 'swmatch.reason.stale_match',
  handoff_expired: 'swmatch.reason.handoff_expired',
  class_busy: 'swmatch.reason.class_busy',
  not_connected: 'swmatch.reason.not_connected',
  rate_limited: 'swmatch.reason.rate_limited',
  upstream_unavailable: 'swmatch.reason.upstream_unavailable',
  key_unavailable: 'swmatch.reason.upstream_unavailable',
  outbound_blocked: 'swmatch.reason.upstream_unavailable',
  invalid_input: 'swmatch.reason.invalid_input',
  not_checked: 'swmatch.reason.not_checked',
  busy: 'swmatch.reason.busy',
  quota: 'swmatch.reason.quota',
  analysis_limit: 'swmatch.reason.analysis_limit',
  too_large: 'swmatch.reason.invalid_input',
  idempotency_mismatch: 'swmatch.reason.invalid_input',
  decrypt_failed: 'swmatch.reason.decrypt_failed',
  pool_busy: 'swmatch.reason.pool_busy',
  song_picking_off: 'swmatch.reason.song_picking_off',
  consent_missing: 'swmatch.reason.consent_missing',
  revision_limit: 'swmatch.reason.revision_limit',
  chat_full: 'swmatch.reason.chat_full',
  stale_base: 'swmatch.reason.stale_base',
  swedebeat_rounds: 'swmatch.reason.swedebeat_rounds',
  write_unresolved: 'swmatch.reason.write_unresolved',
}

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

  it('words every code with its own catalogue text, never the fallback, a code or a provider message', () => {
    // Every key but the fallback is the text of some code: a key nobody maps to is dead copy.
    expect(new Set(Object.values(CODES))).toEqual(new Set(KEYS.filter((k) => k !== 'swmatch.reason.fallback')))
    for (const [code, key] of Object.entries(CODES)) {
      const text = swedebeatError(new ApiError(409, code, null, null))
      expect(text, code).toBe(textOf(en, key))
      expect(text, code).not.toBe(textOf(en, 'swmatch.reason.fallback'))
      expect(text, code).not.toContain(code)
    }
    expect(swedebeatError(new ApiError(429, null, null, null))).toBe(en['swmatch.reason.rate_limited'])
    expect(swedebeatError(new ApiError(500, 'something_new', null, null))).toBe(en['swmatch.reason.fallback'])
    expect(swedebeatError(new Error('boom'))).toBe(en['swmatch.reason.fallback'])
  })

  it.each([['sv', sv], ['da', da]] as const)('words every code in the active language (%s)', async (lang, catalogue) => {
    await loadCatalogue(lang)
    setActiveLocale(lang)
    for (const [code, key] of Object.entries(CODES)) {
      expect(swedebeatError(new ApiError(409, code, null, null)), `${lang} ${code}`).toBe(textOf(catalogue, key))
    }
  })
})
