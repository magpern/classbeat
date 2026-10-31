import { describe, expect, it } from 'vitest'
import { decodeSongList, withStartTimes } from './songs'

const song = (position: number, lengthSec: number) => ({
  position,
  title: `Song ${String(position)}`,
  artist: 'A',
  role: 'main',
  bpm: 120,
  lengthSec,
  isNew: false,
  cover: null,
  openSpotify: null,
})

describe('song start times', () => {
  it('lays songs out by the picker rule: the lengths before it less the crossfade at each change', () => {
    const placed = withStartTimes([song(1, 300), song(2, 240), song(3, 200)], 9)
    expect(placed.map((s) => s.startSec)).toEqual([0, 291, 522])
    // The last song ends where the picker's total time says the class ends.
    expect(522 + 200).toBe(300 + 240 + 200 - 2 * 9)
  })

  it('has no overlap without a crossfade and never a negative or odd one', () => {
    expect(withStartTimes([song(1, 100), song(2, 100)], 0).map((s) => s.startSec)).toEqual([0, 100])
    expect(withStartTimes([song(1, 100), song(2, 100)], -5).map((s) => s.startSec)).toEqual([0, 100])
    expect(withStartTimes([song(1, 100), song(2, 100)], Number.NaN).map((s) => s.startSec)).toEqual([0, 100])
  })

  it('is filled in when a song list from the API is decoded', () => {
    const row = (position: number, length_sec: number) => ({
      position, title: 'T', artist: 'A', role: 'main', bpm: 120, length_sec, new: false,
    })
    const decoded = decodeSongList({
      id: 'l', number: 1, status: 'approved', content_hash: 'h', mode: 'pick', target_sec: 500, total_sec: 491,
      crossfade_sec: 9, songs: [row(1, 300), row(2, 200)],
    })
    expect(decoded.songs.map((s) => s.startSec)).toEqual([0, 291])
    const without = decodeSongList({
      id: 'l', number: 1, status: 'approved', content_hash: 'h', mode: 'pick', target_sec: 500, total_sec: 500,
      songs: [row(1, 300), row(2, 200)],
    })
    expect(without.songs.map((s) => s.startSec)).toEqual([0, 300])
  })
})
