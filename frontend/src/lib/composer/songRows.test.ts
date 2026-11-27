import { describe, expect, it } from 'vitest'
import type { DraftSegment } from '../../api/draftTypes'
import type { Song } from '../../api/songs'
import { t } from '../../i18n/t'
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
  it('offers every option other than the assumed one', () => {
    const question = { topic: 'length' as const, text: 'How long?', options: ['30 minutes', '45 minutes', '60 minutes'], assumedIndex: 1, noMusicIndex: null }
    expect(quickAnswers(question, t)).toEqual([
      { index: 0, label: '30 minutes' },
      { index: 2, label: '60 minutes' },
    ])
  })

  it('offers every option of a required question, which assumes none', () => {
    const question = { topic: 'music' as const, text: 'Music?', options: ['Pop hits', 'No music'], assumedIndex: null, noMusicIndex: 1 }
    expect(quickAnswers(question, t).map((a) => a.label)).toEqual(['Pop hits', 'No music'])
  })

  it('labels the closed which_music question from the catalogue, never with its codes', () => {
    const question = { topic: 'music' as const, code: 'which_music' as const, text: '', options: ['my_playlists', 'no_music'], assumedIndex: null, noMusicIndex: 1 }
    expect(quickAnswers(question, t)).toEqual([
      { index: 0, label: 'Use one of my playlists' },
      { index: 1, label: 'No music' },
    ])
  })
})
