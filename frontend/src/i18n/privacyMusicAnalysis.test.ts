import { describe, expect, it } from 'vitest'
import da from './da'
import { en } from './en'
import { LOCALES } from './locales'
import sv from './sv'

// #1571: MOWL plays and times the class; classbeat analyses the music (ADR 0040). The privacy page's
// processor list once said MOWL returned "your playlists and their analysis" (`privacy.processors.li2`).
// No clause of the privacy page or of the draft sub-processors page that names MOWL says it analyses or
// listens to music. A clause ends at a full stop, a colon or a semicolon. classbeat does read MOWL's
// per-track timing data (BPM and the track's section timing, `mowl.library.spotify_analysis`, #1573), so
// the list of requests names it as "timing data", never as MOWL's analysis.
const catalogues: Record<string, Record<string, unknown>> = { en, sv, da }
const ANALYSE: Record<string, RegExp> = { en: /analy[sz]|listen/i, sv: /analys|lyssna/i, da: /analys|lytte/i }
const CLASSBEAT_ANALYSES: Record<string, RegExp> = {
  en: /classbeat analyses the music\./,
  sv: /classbeat analyserar musiken\./,
  da: /classbeat analyserer musikken\./,
}
const TIMING_DATA: Record<string, RegExp> = { en: /timing data MOWL holds/, sv: /tidsdata MOWL har/, da: /tidsdata, MOWL har/ }
// #1575: each item of the list of requests reads on its own: the timing data is one item, separated by
// commas from the playlists before it and from the searches after it ("playlists and timing data ... for
// searches and mixes" read as one object). da also says MOWL "styrer tiden" (times), not "holder tiden".
const SEPARATE_ITEMS: Record<string, RegExp> = {
  en: /\(your playlists, the timing data MOWL holds for their songs, searches for songs, and mixes\)/,
  sv: /\(dina spellistor, den tidsdata MOWL har för låtarna i dem, sökningar efter låtar och mixar\)/,
  da: /\(dine afspilningslister, de tidsdata, MOWL har for sangene på dem, søgninger efter sange og mixes\)/,
}
const NEVER = /^$/ // a locale without a rule fails its test: nothing matches an empty text
const LEGAL = /^(privacy\.|draft\.privacy\.|draft\.subprocessors\.)/

const clauses = (text: string) => text.split(/(?<=[.:;])\s+/)

function mowlAnalyses(catalogue: Record<string, unknown>, analyse: RegExp): string[] {
  return Object.entries(catalogue)
    .filter((entry): entry is [string, string] => LEGAL.test(entry[0]) && typeof entry[1] === 'string')
    .flatMap(([key, text]) => clauses(text).filter((c) => c.includes('MOWL') && analyse.test(c)).map((c) => `${key}: ${c}`))
}

describe('the privacy pages on who analyses the music', () => {
  it('has a rule for every locale in locales.json', () => {
    for (const lang of LOCALES) {
      expect(catalogues[lang], lang).toBeDefined()
      expect(ANALYSE[lang], lang).toBeDefined()
      expect(CLASSBEAT_ANALYSES[lang], lang).toBeDefined()
      expect(TIMING_DATA[lang], lang).toBeDefined()
      expect(SEPARATE_ITEMS[lang], lang).toBeDefined()
    }
  })

  it('da: the sentence on who does what says "musikken" once', () => {
    const li2 = (da as Record<string, unknown>)['privacy.processors.li2']
    expect(typeof li2).toBe('string')
    const sentence = (li2 as string).split('. ').find((part) => part.includes('MOWL afspiller')) ?? ''
    expect(sentence).toMatch(/classbeat analyserer musikken/)
    expect(sentence.match(/musikken/g)).toHaveLength(1)
  })

  for (const lang of LOCALES) {
    const catalogue = catalogues[lang] ?? {}

    it(`${lang}: no clause about MOWL says it analyses the music`, () => {
      expect(mowlAnalyses(catalogue, ANALYSE[lang] ?? NEVER)).toEqual([])
    })

    it(`${lang}: the processor list says classbeat analyses the music`, () => {
      const li2 = catalogue['privacy.processors.li2']
      expect(typeof li2).toBe('string')
      expect(li2).toMatch(CLASSBEAT_ANALYSES[lang] ?? NEVER)
    })

    it(`${lang}: the list of requests separates its items`, () => {
      expect(catalogue['privacy.processors.li2']).toMatch(SEPARATE_ITEMS[lang] ?? NEVER)
    })

    it(`${lang}: the processor list names the timing data classbeat reads from MOWL`, () => {
      expect(catalogue['privacy.processors.li2']).toMatch(TIMING_DATA[lang] ?? NEVER)
    })
  }

  it('da: MOWL "styrer tiden på" the class, it does not "holde tiden"', () => {
    const li2 = String((da as Record<string, unknown>)['privacy.processors.li2'])
    expect(li2).toMatch(/MOWL afspiller dit hold og styrer tiden på det;/)
    expect(li2).not.toMatch(/holder tiden/)
  })

  it('the check allows the timing-data read and refuses a claim that MOWL analyses or listens', () => {
    const ok = { 'privacy.processors.li2': 'MOWL holds timing data for the songs, which classbeat reads. MOWL plays and times your class.' }
    expect(mowlAnalyses(ok, ANALYSE.en ?? NEVER)).toEqual([])
    for (const claim of ['MOWL analyses the music.', 'MOWL listens to your songs.', 'MOWL relays Spotify analysis of the songs.'])
      expect(mowlAnalyses({ 'privacy.processors.li2': claim }, ANALYSE.en ?? NEVER), claim).toHaveLength(1)
  })

  it('the check catches the old wording', () => {
    const old = { 'privacy.processors.li2': 'MOWL receives requests when classbeat reads your music (your playlists and their analysis).' }
    expect(mowlAnalyses(old, ANALYSE.en ?? NEVER)).toHaveLength(1)
  })
})
