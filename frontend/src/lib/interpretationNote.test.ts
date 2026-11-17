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

  it('words the music taste and what a song pick could not keep to (issue #1133)', () => {
    const note = interpretationNote([
      item('music_era.0', 'explicit', { from: 1980, to: 1989 }, { quote: '80s' }),
      item('music_language', 'explicit', 'sv', { quote: 'Swedish' }),
      item('music_genre.0', 'inferred', null, { quote: 'disco' }),
      item('music_hits', 'explicit', true),
      item('music_era.2', 'not_as_asked', { from: 2000, to: 2009, found: 0 }, { reasonCode: 'era_too_few' }),
      item('music_taste', 'not_as_asked', { songs: 3 }, { reasonCode: 'songs_outside_eras' }),
      item('music_length', 'not_as_asked', { total_min: 15, target_min: 45 }, { reasonCode: 'songs_too_short' }),
      item('music_era.1', 'not_as_asked', { from: 1990, to: 1999 }, { reasonCode: 'taste_needs_pick' }),
    ])
    expect(note.asked.map((l) => l.text)).toEqual(['Music from: 1980 to 1989', 'Song language: Swedish', 'Hit songs'])
    expect(note.assumed.map((l) => [l.text, l.quote])).toEqual([['Music style', 'disco']])
    expect(note.notAsked.map((l) => l.text)).toEqual([
      'Music from: 2000 to 2009. We found too few songs from these years on Spotify.',
      'Music. Some songs are from other years than you asked for.',
      'Song list: 15 of 45 minutes. The songs are shorter than the class.',
      'Music from: 1990 to 1999. Only songs we pick for you can follow the music you asked for.',
    ])
  })

  it('words a taste change a revise keeps out (issue #1140)', () => {
    const note = interpretationNote([
      item('music_era.0', 'not_as_asked', { from: 1990, to: 1999 }, { reasonCode: 'taste_on_revise' }),
    ])
    expect(note.notAsked.map((l) => l.text)).toEqual([
      'Music from: 1990 to 1999. A change keeps the music; use Change songs to pick other songs.',
    ])
  })

  it('shows no taste value it does not know', () => {
    const note = interpretationNote([
      item('music_era.0', 'explicit', { from: '<b>', to: 1989 }),
      item('music_language', 'explicit', '<script>'),
      item('music_unknown.0', 'explicit', 'x'),
    ])
    expect(note.asked.map((l) => l.text)).toEqual(['Music from', 'Song language', 'Something in your request'])
  })
})
