import { describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'

// ADR 0028 section 2 for the job failure texts (`job.failure.<code>.title` and `.detail`): a title is
// at most 8 words, a detail (what happened and what to do) at most 20, "classbeat" is never the subject,
// and en, sv and da each have every key. The two Swedebeat mix failures say the same as the server
// sentences they mirror (`sentence.job.media_host_not_allowed`, `sentence.job.analysis_limit`).

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

const KEYS = (Object.keys(en) as CatalogueKey[]).filter((k) => k.startsWith('job.failure.'))
const limitOf = (key: string) => (key.endsWith('.title') ? 8 : 20)
const catalogues = (): [string, Catalogue][] => [['en', en], ['sv', sv], ['da', da]]

const textOf = (catalogue: Catalogue, key: CatalogueKey): string => {
  const message = catalogue[key]
  if (typeof message !== 'string') throw new Error(`${key} is missing or not a plain string`)
  return message
}

describe('job failure copy', () => {
  it('is not checked against an empty set of keys', () => {
    expect(KEYS.length).toBeGreaterThan(60)
    expect(KEYS.filter((k) => k.endsWith('.detail')).length).toBe(KEYS.filter((k) => k.endsWith('.title')).length)
    expect(KEYS.every((k) => k.endsWith('.title') || k.endsWith('.detail'))).toBe(true)
    expect(KEYS).toContain('job.failure.media_host_not_allowed.detail')
    expect(words('one {two} three')).toBe(3)
  })

  it('has every key in all three languages', () => {
    for (const [lang, catalogue] of [['sv', sv], ['da', da]] as const) {
      expect(KEYS.filter((k) => typeof catalogue[k] !== 'string'), lang).toEqual([])
    }
  })

  it('keeps a title within 8 words and a detail within 20, in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of KEYS) {
        expect(words(textOf(catalogue, key)), `${lang} ${key}`).toBeLessThanOrEqual(limitOf(key))
      }
    }
  })

  it('never makes the product name the subject of a sentence', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of KEYS) {
        expect(textOf(catalogue, key), `${lang} ${key}`).not.toMatch(/classbeat/i)
      }
    }
  })

  it('says the same about an unanalysed mix as the server sentence', () => {
    const detail = 'job.failure.media_host_not_allowed.detail'
    const inAccount = { en: /in your MOWL account/, sv: /finns i ditt MOWL-konto/, da: /ligger i din MOWL-konto/ }
    const analysedBefore = { en: /analysed before/, sv: /analyserats tidigare/, da: /analyseret før/ }
    for (const [lang, catalogue] of catalogues()) {
      const l = lang as 'en' | 'sv' | 'da'
      for (const text of [textOf(catalogue, detail), textOf(catalogue, 'sentence.job.media_host_not_allowed')]) {
        expect(text, `${lang} ${text}`).toMatch(inAccount[l])
        expect(text, `${lang} ${text}`).toMatch(analysedBefore[l])
      }
      // The old wording said the mix "was added" and "could not be analysed", which no longer matches.
      expect(textOf(catalogue, detail), lang).not.toMatch(/was added|lades till|blev føjet/)
      // Retrying cannot help: the server's media allow-list decides, so the failure does not suggest it.
      expect(textOf(catalogue, detail), lang).not.toMatch(/try again|försök igen|prøv igen/i)
    }
  })

  it('keeps the daily limit of the failure and of the sentence in step', () => {
    const next = { en: /Try again tomorrow, or use one analysed before/, sv: /Prova igen i morgon eller använd en som analyserats tidigare/, da: /Prøv igen i morgen, eller brug et tidligere analyseret mix/ }
    for (const [lang, catalogue] of catalogues()) {
      const l = lang as 'en' | 'sv' | 'da'
      expect(textOf(catalogue, 'job.failure.analysis_limit.detail'), lang).toMatch(next[l])
      expect(textOf(catalogue, 'sentence.job.analysis_limit'), lang).toMatch(next[l])
      expect(textOf(catalogue, 'sentence.job.analysis_limit'), lang).toMatch(/10/)
    }
  })

  it('tells a refused save that nothing was written', () => {
    const nothing = { en: /Nothing was written/, sv: /Inget skrevs/, da: /Intet blev skrevet/ }
    for (const code of ['program_changed', 'program_missing'] as const) {
      for (const [lang, catalogue] of catalogues()) {
        const l = lang as 'en' | 'sv' | 'da'
        expect(textOf(catalogue, `job.failure.${code}.detail`), `${lang} ${code}`).toMatch(nothing[l])
      }
    }
  })
})
