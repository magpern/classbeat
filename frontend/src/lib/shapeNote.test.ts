import { describe, expect, it } from 'vitest'
import type { InterpretationItem } from '../api/interpretationItems'
import { interpretationNote } from './interpretationNote'
import { isUntagged } from './shapeNote'

function item(at: number, value: unknown, extra: Partial<InterpretationItem> = {}): InterpretationItem {
  return { path: `shape.${String(at)}`, origin: 'explicit', reasonCode: null, value, quote: null, ...extra }
}

// The original request's reading (issue #1494): six sections, two of them ranges.
const ORIGINAL: InterpretationItem[] = [
  item(0, { kind: 'warmup', trend: 'rising' }, { quote: 'starts off slow and builds up the momentum' }),
  item(1, { kind: 'climb', count: { min: 2, max: 2 } }, { quote: 'a hilly section' }),
  item(2, { kind: 'efforts', effort: 'hard', count: { min: 5, max: 7 }, length: 'short', load: 'heavy' }, { quote: '5-7 short heavy bursts' }),
  item(3, { kind: 'flat' }, { quote: 'back on the flats' }),
  item(4, { kind: 'climb' }, { quote: 'another climb' }),
  item(5, { kind: 'efforts', count: { min: 2, max: 4 } }, { quote: 'a few more efforts' }),
]

describe('the shape of the ride in the reading', () => {
  it('words the original request section by section, ranges as ranges, in the instructor\'s words', () => {
    const note = interpretationNote(ORIGINAL)
    expect(note.shape.map((l) => l.text)).toEqual([
      '1. Warm-up, building up',
      '2. 2 climbs',
      '3. 5 to 7 short, heavy efforts, hard',
      '4. Flat road',
      '5. Climb',
      '6. 2 to 4 efforts',
    ])
    expect(note.shape[0]?.quote).toBe('starts off slow and builds up the momentum')
    expect(note.asked).toEqual([])
    expect(note.shape.map((l) => l.text).join(' ')).not.toMatch(/\b0 (climbs|sprints)\b/)
  })

  it('says what classbeat chooses and that it adds a cool-down when the shape has none', () => {
    expect(interpretationNote(ORIGINAL).shapeNotes).toEqual([
      'classbeat picks the number within your ranges and how long each part is.',
      'classbeat adds a cool-down.',
    ])
    const full = [item(0, { kind: 'warmup' }, { quote: 'q' }), item(1, { kind: 'cooldown' })]
    expect(interpretationNote(full).shapeNotes).toEqual([])
    expect(interpretationNote([item(0, { kind: 'flat' }, { quote: 'q' })]).shapeNotes).toEqual(['classbeat adds a warm-up and a cool-down.'])
  })

  it('claims nothing about what classbeat adds or chooses for a reading with every section but none of the instructor\'s words', () => {
    // Sections 0 to 2 in order (a whole-ride shape), a range and a missing cool-down, but no quote: not a first reading.
    const noQuotes = ORIGINAL.slice(0, 3).map((i) => ({ ...i, quote: null }))
    expect(interpretationNote(noQuotes).shape).toHaveLength(3)
    expect(interpretationNote(noQuotes).shapeNotes).toEqual([])
    // The same items with one quote kept do get the notes.
    const oneQuote = noQuotes.map((i, n) => (n === 0 ? { ...i, quote: 'starts off slow' } : i))
    expect(interpretationNote(oneQuote).shapeNotes).toHaveLength(2)
  })

  it('words a revised reading: only the touched section, no quote, {7,7} as 7', () => {
    const note = interpretationNote([item(2, { kind: 'efforts', effort: 'hard', count: { min: 7, max: 7 }, length: 'short', load: 'heavy' }, { origin: 'inferred' })])
    expect(note.shape.map((l) => l.text)).toEqual(['3. 7 short, heavy efforts, hard'])
    // A partial reading never claims what classbeat adds or chooses for the whole ride.
    expect(note.shapeNotes).toEqual([])
  })

  it('puts the sections in order whatever the order of the items', () => {
    const note = interpretationNote([item(1, { kind: 'flat' }), item(0, { kind: 'warmup', minutes: 10 })])
    expect(note.shape.map((l) => l.text)).toEqual(['1. Warm-up, 10 min', '2. Flat road'])
  })

  it('leaves out values it does not know and never shows server text', () => {
    const note = interpretationNote([item(0, { kind: '<b>x</b>' }), item(1, { kind: 'climb', count: { min: 9, max: 2 }, trend: 'sideways', minutes: 500 })])
    expect(note.shape.map((l) => l.text)).toEqual(['2. Climb'])
    expect(note.asked.map((l) => l.text)).toEqual(['Section 1'])
  })

  it('words an untagged draft as one line about the ride, not about section 1', () => {
    const note = interpretationNote([item(0, { kind: 'warmup' }, { origin: 'not_as_asked', reasonCode: 'shape_untagged' })])
    expect(note.notAsked.map((l) => l.text)).toEqual(["This version does not follow your ride's parts."])
  })

  it('words a section 1 miss (shape_off on shape.0, also an older untagged item) as that section', () => {
    const note = interpretationNote([item(0, { kind: 'warmup' }, { origin: 'not_as_asked', reasonCode: 'shape_off' })])
    expect(note.notAsked.map((l) => l.text)).toEqual(['Section 1: Warm-up. This version does not match.'])
    expect(isUntagged([item(0, { kind: 'warmup' }, { origin: 'not_as_asked', reasonCode: 'shape_off' })])).toBe(false)
    expect(isUntagged([item(0, { kind: 'warmup' }, { origin: 'not_as_asked', reasonCode: 'shape_untagged' })])).toBe(true)
  })

  it('shows the other sections of a section 1 miss as read, and the miss on its own line', () => {
    const note = interpretationNote([
      item(0, { kind: 'warmup' }, { origin: 'not_as_asked', reasonCode: 'shape_off' }),
      item(1, { kind: 'climb' }, { quote: 'a hill' }),
    ])
    expect(note.notAsked.map((l) => l.text)).toEqual(['Section 1: Warm-up. This version does not match.'])
    expect(note.shape.map((l) => l.text)).toEqual(['2. Climb'])
  })

  it('lists the sections of an untagged revision neutrally, never as asked or assumed', () => {
    const note = interpretationNote([
      item(0, { kind: 'warmup' }, { origin: 'not_as_asked', reasonCode: 'shape_untagged' }),
      item(1, { kind: 'climb' }, { quote: 'a hill' }),
      item(2, { kind: 'flat' }, { origin: 'inferred' }),
    ])
    expect(note.notAsked.map((l) => l.text)).toEqual(["This version does not follow your ride's parts."])
    expect(note.shape.map((l) => l.text)).toEqual(['2. Climb', '3. Flat road'])
    expect(note.asked).toEqual([])
    expect(note.assumed).toEqual([])
  })

  it('words Not as asked on a section, with what was found when the record says', () => {
    const note = interpretationNote([
      item(2, { kind: 'efforts', count: { min: 7, max: 7 } }, { origin: 'not_as_asked', reasonCode: 'shape_off', found: 5 }),
      item(3, { kind: 'climb' }, { origin: 'not_as_asked', reasonCode: 'shape_off' }),
      item(4, { kind: 'flat' }, { origin: 'not_as_asked', reasonCode: 'cycling_only' }),
      item(5, null, { origin: 'not_as_asked', reasonCode: 'shape_too_large' }),
    ])
    expect(note.notAsked.map((l) => l.text)).toEqual([
      'Section 3: you asked for 7 efforts; this version has 5',
      'Section 4: Climb. This version does not match.',
      'Section 5: Flat road. The shape of a ride is for cycling classes only.',
      'Section 6. The ride has more parts than we can keep.',
    ])
    expect(note.shape).toEqual([])
  })
})
