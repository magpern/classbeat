import { beforeAll, afterAll, describe, expect, it } from 'vitest'
import { LOCALES } from '../i18n/locales'
import { loadCatalogue, resetCatalogues, translate } from '../i18n/t'
import { disagreements, measures } from '../test/exampleMeasures'
import { EXAMPLES, EXAMPLE_FIELDS, exampleKey, exampleMetaKeys } from './examples'

// "Same source" (ADR 0030, PP5): every number a page writes about an example, in every language,
// is the number the class itself has. The words come from the catalogue and the class from
// examples.json, so editing one without the other fails here.

beforeAll(async () => {
  for (const lang of LOCALES) await loadCatalogue(lang)
})
afterAll(() => {
  resetCatalogues()
})

describe('example copy agrees with the class', () => {
  for (const example of EXAMPLES) {
    it.each(LOCALES.map((l) => [l]))(`${example.slug} in %s`, (lang) => {
      const keys = [...EXAMPLE_FIELDS.map((f) => exampleKey(example.slug, f)), ...Object.values(exampleMetaKeys(example.slug))]
      for (const key of keys) {
        expect(disagreements(translate(lang, key), example), key).toEqual([])
      }
    })

    it(`${example.slug}: the prompt states the length, and the first number is a real measurement`, () => {
      const prompt = measures(translate('en', exampleKey(example.slug, 'prompt')))
      expect(prompt.minutes).toEqual([example.brief.length_min])
    })
  }
})

describe('disagreements', () => {
  it('does not take an interval\'s length for a stretch of the class in minutes', () => {
    let checked = 0
    for (const example of EXAMPLES) {
      const stretches = new Set([example.segments.reduce((sum, s) => sum + s.intervals.reduce((a, i) => a + i.sec, 0), 0) / 60])
      for (const s of example.segments) stretches.add(s.intervals.reduce((a, i) => a + i.sec, 0) / 60)
      for (const i of example.segments.flatMap((s) => s.intervals)) {
        if (i.sec % 60 !== 0 || stretches.has(i.sec / 60)) continue
        checked++
        expect(disagreements(`${String(i.sec / 60)} minutes of work`, example)).toHaveLength(1)
      }
    }
    expect(checked).toBeGreaterThan(0)
  })
})

describe('measures', () => {
  it('reads minutes, seconds, counts and zones', () => {
    expect(measures('A 45 minute ride with three climbs')).toMatchObject({ minutes: [45], counts: [{ noun: 'climbs', n: 3 }] })
    expect(measures('5 rounds of 30 seconds, then six sprints')).toMatchObject({
      seconds: [30],
      counts: [
        { noun: 'sprints', n: 6 },
        { noun: 'repeats', n: 5 },
      ],
    })
    expect(measures('Zones 3 and 4, then zone 5').zones).toEqual([3, 4, 5])
    expect(measures('30 minuter pass i zon 2').minutes).toEqual([30])
    expect(measures('Tre klättringar i zon 3 och 4').counts).toEqual([{ noun: 'climbs', n: 3 }])
    expect(measures('Tre stigninger i zone 3 og 4').zones).toEqual([3, 4])
    expect(measures('Fem omgångar med 30 sekunder, sedan sex sprintar').counts).toHaveLength(2)
  })

  it('does not take a decade for a measurement', () => {
    expect(measures('A 90s party ride')).toMatchObject({ minutes: [], seconds: [], counts: [] })
  })

  it('catches a number that is not in the class', () => {
    const hill = EXAMPLES[0]
    if (!hill) throw new Error('no example')
    expect(disagreements('A 50 minute ride with four climbs in zone 7', hill)).toEqual([
      '50 minutes is not in the class',
      'zone 7 is not used by the class',
      '4 climbs is not in the class',
    ])
    expect(disagreements('A 45 minute ride with three climbs in zone 3', hill)).toEqual([])
  })
})
