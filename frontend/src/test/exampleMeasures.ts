import type { Example } from '../publicSite/examples'
import { flatten } from '../lib/profile'

// Reads the measurements out of the words around an example class (ADR 0030, PP5) so a test can
// compare them with the class itself. A number is a measurement only next to a unit or a noun.
// A decade ("90s") is a decade, not 90 seconds, and "Zone 3" names a zone.

export type CountNoun = 'climbs' | 'sprints' | 'repeats'
export interface Measures {
  minutes: number[]
  seconds: number[]
  counts: { noun: CountNoun; n: number }[]
  zones: number[]
}

const WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
  ett: 1, två: 2, tre: 3, fyra: 4, fem: 5, sex: 6, sju: 7, åtta: 8, nio: 9, tio: 10,
  et: 1, to: 2, fire: 4, seks: 6, syv: 7, otte: 8, ni: 9, ti: 10,
}
const NUM = String.raw`(\d+|${Object.keys(WORDS).join('|')})`
const num = (s: string) => (/^\d+$/.test(s) ? Number(s) : (WORDS[s.toLowerCase()] ?? NaN))
const all = (re: RegExp, text: string) => [...text.matchAll(re)]

const NOUNS: Record<CountNoun, string> = {
  climbs: String.raw`(?:climbs?|backar|backe|bakker|bakke)`,
  sprints: String.raw`(?:sprints?|sprintar|sprinter)`,
  repeats: String.raw`(?:rounds?|omgång(?:ar)?|runder?|intervals?|intervaller|intervaler|repeats?|gentagelser|upprepningar)`,
}

export function measures(raw: string): Measures {
  // "90s" is a decade; "30s" is left alone because it is a plausible duration.
  const text = raw.replace(/\b[5-9]0s\b/g, 'DECADE').toLowerCase()
  const minutes = all(new RegExp(String.raw`\b${NUM}[ -]*(?:minutes?|min|minuter|minutter|minut)\b`, 'giu'), text).map((m) =>
    num(m[1] ?? ''),
  )
  const seconds = all(new RegExp(String.raw`\b${NUM}[ -]*(?:seconds?|secs?|sekunder|sekund|sek)\b`, 'giu'), text).map((m) =>
    num(m[1] ?? ''),
  )
  const counts = (Object.keys(NOUNS) as CountNoun[]).flatMap((noun) =>
    all(new RegExp(String.raw`\b${NUM}\s+(?:(?!(?:minut|second|sek|min\b)[\p{L}]*)[\p{L}-]+\s+)?${NOUNS[noun]}\b`, 'giu'), text).map((m) => ({
      noun,
      n: num(m[1] ?? ''),
    })),
  )
  const zones = all(/\b(?:zones?|zon|zoner)\s+(\d)(?:\s*(?:and|och|og|to|till|til|[-–])\s*(\d))?/giu, text).flatMap((m) =>
    [m[1], m[2]].filter((z): z is string => z !== undefined).map(Number),
  )
  return { minutes, seconds, counts, zones }
}

/** What the class itself says: the numbers a sentence about it may use. */
export interface Truth {
  minutes: Set<number>
  seconds: Set<number>
  climbs: number
  sprints: number
  repeats: Set<number>
  zones: Set<number>
}

export function truth(example: Example): Truth {
  const bars = flatten(example.segments)
  const total = bars.reduce((sum, b) => sum + b.sec, 0)
  const minutes = new Set<number>([total / 60])
  const seconds = new Set<number>()
  const repeats = new Set<number>()
  for (const segment of example.segments) {
    const sec = segment.intervals.reduce((sum, i) => sum + i.sec, 0)
    if (sec % 60 === 0) minutes.add(sec / 60)
    const seen = new Map<string, number>()
    for (const i of segment.intervals) {
      seconds.add(i.sec)
      if (i.sec % 60 === 0) minutes.add(i.sec / 60)
      const key = `${String(i.zone)}/${String(i.sec)}`
      seen.set(key, (seen.get(key) ?? 0) + 1)
    }
    for (const n of seen.values()) if (n > 1) repeats.add(n)
  }
  return {
    minutes,
    seconds,
    climbs: example.segments.filter((s) => /^climb \d/i.test(s.name)).length,
    sprints: bars.filter((b) => b.zone >= 6 && b.sec <= 30).length,
    repeats,
    zones: new Set(bars.map((b) => b.zone)),
  }
}

/** The disagreements between a text and the class, as readable lines; empty when they agree. */
export function disagreements(text: string, example: Example): string[] {
  const m = measures(text)
  const t = truth(example)
  const out: string[] = []
  for (const v of m.minutes) if (!t.minutes.has(v)) out.push(`${String(v)} minutes is not in the class`)
  for (const v of m.seconds) if (!t.seconds.has(v)) out.push(`${String(v)} seconds is not in the class`)
  for (const v of m.zones) if (!t.zones.has(v)) out.push(`zone ${String(v)} is not used by the class`)
  for (const { noun, n } of m.counts) {
    const ok = noun === 'climbs' ? n === t.climbs : noun === 'sprints' ? n === t.sprints : t.repeats.has(n)
    if (!ok) out.push(`${String(n)} ${noun} is not in the class`)
  }
  return out
}
