import { describe, expect, it } from 'vitest'
import { MAX_REQUEST } from '../../lib/startRequest'
import { SAFE_MINUTES, SURPRISES, fittingSurprises, pickSurprise, surpriseLocale } from './surprise'

const LOCALES = ['en', 'sv', 'da'] as const

describe('Surprise me', () => {
  it('has a description in every language, each naming its own length and fitting the request limit', () => {
    expect(SURPRISES.length).toBeGreaterThanOrEqual(5)
    for (const entry of SURPRISES) {
      for (const locale of LOCALES) {
        const text = entry.text[locale]
        expect(text.trim(), locale).not.toBe('')
        expect(text.length, locale).toBeLessThanOrEqual(MAX_REQUEST)
        expect(text, locale).toContain(String(entry.minutes))
      }
    }
  })

  it('has no repeated description', () => {
    for (const locale of LOCALES) {
      const texts = SURPRISES.map((entry) => entry.text[locale])
      expect(new Set(texts).size).toBe(texts.length)
    }
  })

  it('always picks a table entry in the language asked for', () => {
    for (const locale of LOCALES) {
      for (let i = 0; i < SURPRISES.length * 2; i++) {
        const picked = pickSurprise(locale, 180, () => i / (SURPRISES.length * 2))
        expect(SURPRISES.map((entry) => entry.text[locale])).toContain(picked)
      }
    }
  })

  it('reaches every entry the plan allows, with the first and the last random values', () => {
    const picks = new Set([0, 0.2, 0.4, 0.6, 0.8, 0.9999].map((r) => pickSurprise('en', 180, () => r)))
    expect(picks.size).toBe(SURPRISES.length)
  })

  it('stays within the plan length', () => {
    for (const max of [20, 30, 45, 60, 180]) {
      for (const entry of fittingSurprises(max)) expect(entry.minutes).toBeLessThanOrEqual(max)
    }
    const picked = new Set([0, 0.5, 0.99].map((r) => pickSurprise('en', 30, () => r)))
    for (const text of picked) {
      const entry = SURPRISES.find((e) => e.text.en === text)
      expect(entry?.minutes).toBeLessThanOrEqual(30)
    }
  })

  it('assumes a short class while the plan is unknown, and falls back to the shortest when none fits', () => {
    for (const entry of fittingSurprises(null)) expect(entry.minutes).toBeLessThanOrEqual(SAFE_MINUTES)
    const shortest = Math.min(...SURPRISES.map((entry) => entry.minutes))
    expect(fittingSurprises(5).every((entry) => entry.minutes === shortest)).toBe(true)
  })

  it('reads English for a language it has no table for', () => {
    expect(surpriseLocale('sv')).toBe('sv')
    expect(surpriseLocale('da')).toBe('da')
    expect(surpriseLocale('de')).toBe('en')
  })
})
