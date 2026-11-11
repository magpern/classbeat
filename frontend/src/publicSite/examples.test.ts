import { describe, expect, it } from 'vitest'
import { en } from '../i18n/en'
import { EXAMPLES, EXAMPLE_FIELDS, EXAMPLE_SLUGS, exampleKey, exampleMetaKeys, exampleProfile, exampleSeconds, parseExamples } from './examples'
import raw from './examples.json'

const clone = (): { version: number; examples: Record<string, unknown>[] } =>
  JSON.parse(JSON.stringify(raw)) as { version: number; examples: Record<string, unknown>[] }

const first = (doc: ReturnType<typeof clone>) => doc.examples[0] as { slug: string; segments: { intervals: Record<string, unknown>[]; kind: string }[] }

describe('examples.json', () => {
  it('holds the four examples, in order, with the right sports', () => {
    expect(EXAMPLES.map((e) => e.slug)).toEqual([...EXAMPLE_SLUGS])
    expect(EXAMPLES.map((e) => e.sport)).toEqual(['cycling', 'cycling', 'cycling', 'running'])
  })

  it.each(EXAMPLES.map((e) => [e.slug, e] as const))('%s: the segments add up to the length in the brief', (_slug, e) => {
    expect(exampleSeconds(e)).toBe(e.brief.length_min * 60)
    expect(exampleProfile(e).reduce((sum, [, sec]) => sum + sec, 0)).toBe(exampleSeconds(e))
  })

  it('has no game, and none on a running class', () => {
    for (const e of EXAMPLES) for (const s of e.segments) for (const i of s.intervals) expect(i.game).toBeUndefined()
  })

  it('has English text for every field and head of every example', () => {
    for (const slug of EXAMPLE_SLUGS) {
      for (const field of EXAMPLE_FIELDS) expect(en, `${slug} ${field}`).toHaveProperty([exampleKey(slug, field)])
      const meta = exampleMetaKeys(slug)
      expect(en).toHaveProperty([meta.title])
      expect(en).toHaveProperty([meta.description])
    }
  })
})

describe('parseExamples', () => {
  it('accepts the shipped file', () => {
    expect(parseExamples(raw)).toHaveLength(4)
  })

  it('refuses a wrong version, a bad slug, a wrong order and a missing example', () => {
    const wrongVersion = clone()
    wrongVersion.version = 2
    expect(() => parseExamples(wrongVersion)).toThrow(/version/)
    const badSlug = clone()
    first(badSlug).slug = '../x'
    expect(() => parseExamples(badSlug)).toThrow(/slug/)
    const swapped = clone()
    swapped.examples.reverse()
    expect(() => parseExamples(swapped)).toThrow(/in order/)
    const fewer = clone()
    fewer.examples.pop()
    expect(() => parseExamples(fewer)).toThrow(/in order/)
    expect(() => parseExamples(null)).toThrow()
  })

  it('refuses a total that is not the brief length', () => {
    const doc = clone()
    const interval = first(doc).segments[0]?.intervals[0]
    if (interval) interval.sec = Number(interval.sec) + 5
    expect(() => parseExamples(doc)).toThrow(/add up/)
  })

  it('refuses a game, a zone out of range and a last segment that is not the cool-down', () => {
    const game = clone()
    const g = first(game).segments[0]?.intervals[0]
    if (g) g.game = 'sprint'
    expect(() => parseExamples(game)).toThrow()
    const zone = clone()
    const z = first(zone).segments[0]?.intervals[0]
    if (z) z.zone = 9
    expect(() => parseExamples(zone)).toThrow()
    const last = clone()
    const l = first(last).segments.at(-1)
    if (l) l.kind = 'main'
    expect(() => parseExamples(last)).toThrow(/cool-down/)
  })

  it('refuses a running class with a cadence or a bike zone', () => {
    const rpm = clone()
    const running = rpm.examples[3] as { segments: { intervals: Record<string, unknown>[] }[] }
    const r = running.segments[0]?.intervals[0]
    if (r) r.rpm = 90
    expect(() => parseExamples(rpm)).toThrow()
    const zone = clone()
    const rz = (zone.examples[3] as typeof running).segments[0]?.intervals[0]
    if (rz) rz.zone = 6
    expect(() => parseExamples(zone)).toThrow()
  })
})
