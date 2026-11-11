import { describe, expect, it } from 'vitest'
import { en } from './en'
import { publicEn } from './public.en'
import type { CatalogueKey } from './en'

// ADR 0028 section 2 for the public pages How it works and Features (PP4, #893): second person, "we",
// lowercase "classbeat". Headings and labels keep their short limits. Body text is at most 25 words,
// except the long-form allowance below: a step or a feature explanation may run to 35, because it says
// what happens and what it needs in one place (as the help answers do).

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

const HEADING_LIMIT = 8
const BODY_LIMIT = 25
const LONG_FORM_LIMIT = 35

/** The body texts that may run past the usual 25 words. Keep this list short. */
const LONG_FORM: readonly CatalogueKey[] = [
  'how.step.describe.text',
  'how.step.generate.text',
  'how.step.refine.text',
  'how.step.save.text',
  'how.connect.spotify.text',
]

const KEYS = Object.keys(publicEn) as (keyof typeof publicEn)[]
const isHeading = (key: string) => /(\.title|\.name|\.group\.\w+|^pub\.nav\.\w+|^claims\.class\.\w+)$/.test(key)
const isMeta = (key: string) => key.startsWith('meta.')

describe('public page copy', () => {
  it('is in the English catalogue and is not checked against an empty set', () => {
    expect(KEYS.length).toBeGreaterThan(60)
    for (const key of KEYS) expect(en[key], key).toBe(publicEn[key])
  })

  it('keeps headings and labels short', () => {
    for (const key of KEYS.filter((k) => isHeading(k) && !isMeta(k))) {
      expect(words(publicEn[key]), key).toBeLessThanOrEqual(HEADING_LIMIT)
    }
  })

  it('keeps body text within 25 words, or 35 for the named long-form texts', () => {
    for (const key of KEYS.filter((k) => !isHeading(k) && !isMeta(k))) {
      const limit = (LONG_FORM as readonly string[]).includes(key) ? LONG_FORM_LIMIT : BODY_LIMIT
      expect(words(publicEn[key]), `${key}: ${publicEn[key]}`).toBeLessThanOrEqual(limit)
    }
  })

  it('names only long-form texts that exist and need the allowance', () => {
    for (const key of LONG_FORM) {
      expect(KEYS as string[], key).toContain(key)
      expect(words(publicEn[key as keyof typeof publicEn]), `${key} would fit the normal limit`).toBeGreaterThan(BODY_LIMIT)
    }
  })

  it('writes the product name in lowercase and never as the subject of a sentence', () => {
    for (const key of KEYS) {
      expect(publicEn[key], key).not.toMatch(/\bClassbeat\b/)
      expect(publicEn[key], key).not.toMatch(/(^|[.!?:;,(]\s*)classbeat\b/)
    }
  })
})
