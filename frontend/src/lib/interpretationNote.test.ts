import { describe, expect, it } from 'vitest'
import type { InterpretationItem } from '../api/interpretationItems'
import { interpretationNote } from './interpretationNote'

function item(path: string, origin: InterpretationItem['origin'], value: unknown, extra: Partial<InterpretationItem> = {}): InterpretationItem {
  return { path, origin, reasonCode: null, value, quote: null, ...extra }
}

describe('interpretationNote', () => {
  it('sorts items into the three parts and words them from the catalogue', () => {
    const note = interpretationNote([
      item('sport', 'explicit', 'running'),
      item('games.0', 'explicit', { type: 'fast_lane', duration_sec: 180 }),
      item('ftp_test', 'not_as_asked', 20, { reasonCode: 'ftp_missing' }),
      item('class_type', 'inferred', 'hiit'),
      item('music', 'default', 'soundless'),
    ])
    expect(note.asked.map((l) => l.text)).toEqual(['Sport: Running', 'Fast Lane, 3 min'])
    expect(note.notAsked.map((l) => l.text)).toEqual(['FTP test: 20 minutes. There is no FTP on your profile.'])
    expect(note.assumed.map((l) => l.text)).toEqual(['Class type: HIIT', 'Music: No music'])
  })

  it('never shows a value or a reason it does not know, only the neutral words', () => {
    const note = interpretationNote([
      item('<img src=x>', 'explicit', '<b>x</b>'),
      item('length_min', 'not_as_asked', 'ninety', { reasonCode: '<script>' }),
      item('games.3', 'explicit', { type: 'nope' }),
    ])
    expect(note.asked.map((l) => l.text)).toEqual(['Something in your request', 'A game'])
    expect(note.notAsked.map((l) => l.text)).toEqual(['Length. It could not be done as asked.'])
  })

  it('keeps the quote apart as the instructor\'s own words', () => {
    const [line] = interpretationNote([item('climbs', 'explicit', 2, { quote: 'two big climbs' })]).asked
    expect(line?.quote).toBe('two big climbs')
  })
})
