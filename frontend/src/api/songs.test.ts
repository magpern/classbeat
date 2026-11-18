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

describe('a composer list (ADR 0038, #1188)', () => {
  const row = (position: number, source?: Record<string, unknown>) => ({
    position, title: 'T', artist: 'A', role: 'climb', bpm: 120, length_sec: 240, new: false, ...(source ? { source } : {}),
  })
  const base = { id: 'l', number: 1, status: 'waiting', content_hash: 'h', mode: 'fill', target_sec: 600, total_sec: 480, crossfade_sec: 9 }

  it('reads each song\'s source: pinned, an attached playlist by its position, or search', () => {
    const decoded = decodeSongList({
      ...base,
      songs: [row(1, { kind: 'pinned' }), row(2, { kind: 'playlist', playlist: 1 }), row(3, { kind: 'search' }), row(4, { kind: 'playlist' })],
    })
    expect(decoded.songs.map((s) => s.source)).toEqual([{ kind: 'pinned' }, { kind: 'playlist', index: 1 }, { kind: 'search' }, null])
  })

  it('starts each song where the preview heard it, and keeps the preview\'s class', () => {
    const content = { name: 'Class', description: '', segments: [] }
    const decoded = decodeSongList({
      ...base,
      songs: [row(1), row(2)],
      preview: { content, songs: [{ start: 0, end: 240 }, { start: 300, end: 540 }] },
    })
    expect(decoded.songs.map((s) => s.startSec)).toEqual([0, 300])
    expect(decoded.preview?.name).toBe('Class')
  })

  it('falls back to the picker rule when the preview spans do not match the songs', () => {
    const content = { name: 'Class', description: '', segments: [] }
    const decoded = decodeSongList({ ...base, songs: [row(1), row(2)], preview: { content, songs: [{ start: 0, end: 240 }] } })
    expect(decoded.songs.map((s) => s.startSec)).toEqual([0, 231])
  })
})
