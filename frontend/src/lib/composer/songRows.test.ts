import { describe, expect, it } from 'vitest'
import type { DraftSegment } from '../../api/draftTypes'
import type { Song } from '../../api/songs'
import { quickAnswers } from './quickAnswers'
import { songRows } from './songRows'

const seg = (name: string, sec: number): DraftSegment => ({
  name,
  kind: 'main',
  intervals: [{ sec, zone: 3, rpm: [80, 90], standing: false, cue: '' }],
})
const song = (position: number, startSec: number, title: string): Song => ({
  position,
  title,
  artist: 'A',
  role: 'main',
  bpm: 120,
  lengthSec: 200,
  startSec,
  isNew: false,
  cover: null,
  openSpotify: null,
})

describe('the segment rows', () => {
  const segments = [seg('Warm-up', 300), seg('Climb', 600), seg('Cool-down', 120)]
  it('lists a song once, in the segment it starts in', () => {
    const rows = songRows(segments, [song(1, 0, 'a'), song(2, 280, 'b'), song(3, 480, 'c')])
    expect(rows.map((r) => r.songs.map((s) => s.title))).toEqual([['a', 'b'], ['c'], []])
  })
  it('gives a segment with no song start no songs, and the last takes a song starting after the end', () => {
    const rows = songRows(segments, [song(1, 0, 'a'), song(2, 5000, 'z')])
    expect(rows[1]!.songs).toEqual([])
    expect(rows[2]!.songs.map((s) => s.title)).toEqual(['z'])
  })
  it('has no songs without a list', () => {
    expect(songRows(segments, null).every((r) => r.songs.length === 0)).toBe(true)
  })
})

describe('the quick answers', () => {
  it('offers lengths other than the assumed one', () => {
    expect(quickAnswers({ question: 'how_long', assumed: 45 })).toEqual([
      { kind: 'length', minutes: 30 },
      { kind: 'length', minutes: 60 },
    ])
  })
  it('offers efforts other than the assumed one', () => {
    expect(quickAnswers({ question: 'how_hard', assumed: 'moderate' })).toEqual([
      { kind: 'effort', effort: 'easy' },
      { kind: 'effort', effort: 'hard' },
    ])
  })
})
