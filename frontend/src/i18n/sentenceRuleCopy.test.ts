import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'

// ADR 0028 section 2 for the texts the backend mirrors: the server sentences (`sentence.*`, written by
// `jobs/sentences.py` and `spotify/errors.py`), the reasons of an error answer (`error.reason.*`) and the
// pattern-note rationales (`class.rule.*`, `design/rules/rationales.py`). An error is at most 20 words
// (what happened and what to do), a rationale at most 16 (ADR 0017 section 4), and "classbeat" is never
// the subject of a sentence. In en, sv and da, and the backend's own English for the same sentences.
// The parity with the backend is checked elsewhere (`sentences.test.ts`, `patternNotes.test.ts`).

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

const ERROR_LIMIT = 20
const RULE_LIMIT = 16

const SCOPE = (Object.keys(en) as CatalogueKey[]).filter((k) => /^(sentence|error\.reason|class\.rule)\./.test(k))
const limitOf = (key: CatalogueKey): number => (key.startsWith('class.rule.') ? RULE_LIMIT : ERROR_LIMIT)

/** Every form of an entry (a plural has several). */
const textsOf = (catalogue: Catalogue, key: CatalogueKey): string[] => {
  const message = catalogue[key] ?? en[key]
  return typeof message === 'string' ? [message] : Object.values(message)
}
const catalogues = (): [string, Catalogue][] => [['en', en], ['sv', sv], ['da', da]]

/** "classbeat" as the subject: it starts a sentence, or follows a colon, a semicolon or an opening bracket. */
const NAME_AS_SUBJECT = /(^|[.!?:;(]\s*)classbeat\b/i

/** Words that make a sentence say what to do next, per language, for the texts the pass rewrote. */
const NEXT_STEP: readonly [CatalogueKey, Readonly<Record<string, RegExp>>][] = [
  ['sentence.spotify.not_allowlisted', { en: /User Management.*add/s, sv: /Lägg till.*User Management/s, da: /Tilføj.*User Management/s }],
  ['sentence.spotify.scope_mismatch', { en: /Connect again/, sv: /Anslut igen/, da: /Opret forbindelse igen/ }],
  ['sentence.spotify.invalid_response', { en: /Connect again/, sv: /Anslut igen/, da: /Opret forbindelse igen/ }],
  ['sentence.spotify.account_changed', { en: /Confirm to switch/, sv: /Bekräfta för att byta/, da: /Bekræft for at skifte/ }],
  ['sentence.job.media_host_not_allowed', { en: /Try one analysed before/, sv: /Prova en som analyserats tidigare/, da: /Prøv et, der er analyseret før/ }],
  ['sentence.job.analysis_limit', { en: /Try again tomorrow/, sv: /Prova igen i morgon/, da: /Prøv igen i morgen/ }],
]

describe('backend-mirrored sentences, error reasons and rule rationales', () => {
  it('is not checked against an empty set of keys', () => {
    // A renamed or removed key must fail here instead of silently dropping out of the guard.
    expect(SCOPE.filter((k) => k.startsWith('sentence.')).length).toBeGreaterThan(25)
    expect(SCOPE.filter((k) => k.startsWith('error.reason.')).length).toBeGreaterThan(25)
    expect(SCOPE.filter((k) => k.startsWith('class.rule.')).length).toBe(27)
    for (const [key] of NEXT_STEP) expect(en[key], key).toBeDefined()
  })

  it('has every key in all three languages', () => {
    for (const [lang, catalogue] of [['sv', sv], ['da', da]] as const) {
      expect(SCOPE.filter((k) => catalogue[k] === undefined), lang).toEqual([])
    }
  })

  it('keeps every error and rationale within its word limit in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE) {
        for (const text of textsOf(catalogue, key)) {
          expect(words(text), `${lang} ${key}: ${text}`).toBeLessThanOrEqual(limitOf(key))
        }
      }
    }
  })

  it('never has classbeat as the subject of a sentence', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE) {
        for (const text of textsOf(catalogue, key)) {
          expect(text, `${lang} ${key}`).not.toMatch(NAME_AS_SUBJECT)
        }
      }
    }
  })

  it('keeps the next step in the sentences that were shortened', () => {
    for (const [key, patterns] of NEXT_STEP) {
      for (const [lang, catalogue] of catalogues()) {
        expect(textsOf(catalogue, key).join(' '), `${lang} ${key}`).toMatch(patterns[lang] ?? /^$/)
      }
    }
  })

  it('keeps the Spotify allow-list sentence truthful about who adds the account', () => {
    // The user owns the Spotify app they connect (they create it and paste its Client ID), so the
    // sentence tells them to add the account themselves, and that the app's owner needs Premium.
    expect(en['sentence.spotify.not_allowlisted']).toMatch(/Premium/)
    expect(sv['sentence.spotify.not_allowlisted']).toMatch(/Premium/)
    expect(da['sentence.spotify.not_allowlisted']).toMatch(/Premium/)
  })

  it('holds the backend English to the same limits', () => {
    const cases = JSON.parse(
      readFileSync(join(import.meta.dirname, '../../../backend/tests/fixtures/sentence_codes.json'), 'utf8'),
    ) as { code: string; english: string }[]
    expect(cases.length).toBeGreaterThan(30)
    for (const { code, english } of cases) {
      expect(words(english), code).toBeLessThanOrEqual(ERROR_LIMIT)
      expect(english, code).not.toMatch(NAME_AS_SUBJECT)
      expect(english, code).not.toMatch(/classbeat/i)
    }
  })

  it('holds the backend rationales to the rationale limit', () => {
    const source = readFileSync(
      join(import.meta.dirname, '../../../backend/src/classbeat/design/rules/rationales.py'),
      'utf8',
    )
    const found = [...source.matchAll(/^\s+"([a-z0-9_]+)":\s*"([^"]+)",?\s*$/gm)]
    expect(found.length).toBeGreaterThan(25)
    for (const [, id, text] of found) {
      expect(words(text ?? ''), id).toBeLessThanOrEqual(RULE_LIMIT)
      expect(text ?? '', id).not.toMatch(NAME_AS_SUBJECT)
    }
  })
})
