import { describe, expect, it } from 'vitest'
import da from './da'
import { en } from './en'
import sv from './sv'

// #1540: MOWL does not listen to or analyse the songs. The consent card before the first song search says
// what happens instead, as the backend's `CONSENT_TEXT` (version 3) does: the working playlist is linked in
// MOWL so the class can be timed to it, and spotdown analyses the songs from their title, artists and length,
// from a source other than Spotify (ADR 0040). The backend test `test_spotify_consent_wording` checks the
// English card against `CONSENT_TEXT`.
const catalogues = { en, sv, da } as const
const ANALYSE = { en: /analy[sz]|listen/i, sv: /analys|lyssna/i, da: /analys|lytte/i } as const
const TIMED = { en: /timed to it/, sv: /tidsättas efter den/, da: /afstemmes efter den/ } as const
const FACTS = {
  en: [/title, artists and length/, /source other than Spotify/],
  sv: [/titel, artister och längd/, /annan källa än Spotify/],
  da: [/titel, kunstnere og længde/, /anden kilde end Spotify/],
} as const

const sentences = (text: string) => text.split(/(?<=[.:])\s+/)

describe('the working playlist consent card', () => {
  for (const lang of ['en', 'sv', 'da'] as const) {
    const message = catalogues[lang]['cw.music.consent.body']
    const body = typeof message === 'string' ? message : ''

    it(`${lang}: no sentence about MOWL says it analyses or listens to the songs`, () => {
      expect(typeof message).toBe('string')
      const aboutMowl = sentences(body).filter((s) => s.includes('MOWL'))
      expect(aboutMowl.length).toBeGreaterThan(0)
      for (const sentence of aboutMowl) expect(sentence).not.toMatch(ANALYSE[lang])
    })

    it(`${lang}: says why it is linked in MOWL and what spotdown gets`, () => {
      expect(body).toMatch(TIMED[lang])
      expect(body).toContain('spotdown')
      for (const fact of FACTS[lang]) expect(body).toMatch(fact)
    })
  }
})
