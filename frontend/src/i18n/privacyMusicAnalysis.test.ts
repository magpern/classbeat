import { describe, expect, it } from 'vitest'
import da from './da'
import { en } from './en'
import sv from './sv'

// #1571: MOWL plays and times the class; classbeat analyses the music (ADR 0040). The privacy page's
// processor list once said MOWL returned "your playlists and their analysis" (`privacy.processors.li2`).
// No clause of the privacy page or of the draft sub-processors page that names MOWL says it analyses or
// listens to music. A clause ends at a full stop, a colon or a semicolon.
const catalogues = { en, sv, da } as const
const ANALYSE = { en: /analy[sz]|listen/i, sv: /analys|lyssna/i, da: /analys|lytte/i } as const
const LEGAL = /^(privacy\.|draft\.privacy\.|draft\.subprocessors\.)/

const clauses = (text: string) => text.split(/(?<=[.:;])\s+/)

function mowlAnalyses(catalogue: Record<string, unknown>, analyse: RegExp): string[] {
  return Object.entries(catalogue)
    .filter((entry): entry is [string, string] => LEGAL.test(entry[0]) && typeof entry[1] === 'string')
    .flatMap(([key, text]) => clauses(text).filter((c) => c.includes('MOWL') && analyse.test(c)).map((c) => `${key}: ${c}`))
}

describe('the privacy pages on who analyses the music', () => {
  for (const lang of ['en', 'sv', 'da'] as const) {
    const catalogue = catalogues[lang] as Record<string, unknown>

    it(`${lang}: no clause about MOWL says it analyses the music`, () => {
      expect(mowlAnalyses(catalogue, ANALYSE[lang])).toEqual([])
    })

    it(`${lang}: the processor list says classbeat analyses the music`, () => {
      const li2 = catalogue['privacy.processors.li2']
      expect(typeof li2).toBe('string')
      expect(li2).toMatch({ en: /classbeat analyses the music\./, sv: /classbeat analyserar musiken\./, da: /classbeat analyserer musikken\./ }[lang])
    })
  }

  it('the check catches the old wording', () => {
    const old = { 'privacy.processors.li2': 'MOWL receives requests when classbeat reads your music (your playlists and their analysis).' }
    expect(mowlAnalyses(old, ANALYSE.en)).toHaveLength(1)
  })
})
