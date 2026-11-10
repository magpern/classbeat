import { describe, expect, it } from 'vitest'
import type { DraftSegment } from '../api/jobs'
import type { Song } from '../api/songs'
import { planSections } from './musicPlan'

const segment = (name: string, ...secs: number[]): DraftSegment => ({
  name,
  kind: 'main',
  intervals: secs.map((sec) => ({ sec, zone: 2, rpm: [80, 90], standing: false, cue: '' })),
})
const song = (position: number, startSec: number, lengthSec: number): Song => ({
  position,
  title: `Song ${String(position)}`,
  artist: 'A',
  role: 'main',
  bpm: 100,
  lengthSec,
  startSec,
  isNew: false,
  cover: null,
  openSpotify: null,
})

describe('planSections', () => {
  const segments = [segment('Warm up', 300), segment('Climb', 600, 300), segment('Cool down', 120)]

  it('puts each song in the sections its block overlaps, on the section’s own clock', () => {
    const songs = [song(1, 0, 241), song(2, 232, 119), song(3, 351, 900)]
    const [warm, climb, cool] = planSections(segments, songs)
    expect(warm).toMatchObject({ name: 'Warm up', startSec: 0, endSec: 300 })
    expect(warm!.songs.map((s) => [s.position, s.startSec])).toEqual([
      [1, 0],
      [2, 232],
    ])
    // Song 2 runs over the change of section, so it is in both; its start is clamped to the section's start.
    expect(climb).toMatchObject({ startSec: 300, endSec: 1200 })
    expect(climb!.songs.map((s) => [s.position, s.startSec])).toEqual([
      [2, 0],
      [3, 51],
    ])
    expect(cool!.songs.map((s) => s.position)).toEqual([3])
  })

  it('does not depend on the order the songs come in, and leaves the input alone', () => {
    const songs = [song(2, 232, 119), song(1, 0, 241)]
    const [warm] = planSections(segments, songs)
    expect(warm!.songs.map((s) => s.position)).toEqual([1, 2])
    expect(songs.map((s) => s.position)).toEqual([2, 1])
  })

  it('gives a section no songs when none reach it, and none for a section with no length', () => {
    const [warm, climb] = planSections([segment('Warm up', 100), segment('Empty'), segment('Climb', 100)], [song(1, 0, 50), song(2, 150, 50)])
    expect(warm!.songs.map((s) => s.position)).toEqual([1])
    expect(climb!.songs).toEqual([])
  })

  it('returns no songs at all for an empty list', () => {
    expect(planSections(segments, []).every((s) => s.songs.length === 0)).toBe(true)
  })
})
