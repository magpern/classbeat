import { describe, expect, it } from 'vitest'
import type { Timeline } from '../../api/studioOutputs'
import { audioKey } from './audioKey'

// What makes the fetched preview clips stale: the stored mix spec and the songs' places in the mix.

function timeline(extra: Partial<Timeline> = {}): Timeline {
  return {
    revisionId: 'r1',
    contentHash: 'aa',
    durationSec: 600,
    workoutSec: 600,
    tracks: [
      { position: 0, startSec: 10, mixStartSec: 0, mixEndSec: 300 },
      { position: 1, startSec: 0, mixStartSec: 292, mixEndSec: 600 },
    ] as Timeline['tracks'],
    hasRepeats: false,
    hints: [],
    crossfades: [],
    intervals: [],
    specDigest: 'spec1',
    proposalRevisionId: null,
    isPlain: false,
    choices: null,
    ...extra,
  }
}

describe('audioKey', () => {
  it('is the same for a workout-only change (another revision, hash or zone length)', () => {
    expect(audioKey(timeline({ revisionId: 'r2', contentHash: 'bb', workoutSec: 540 }))).toBe(audioKey(timeline()))
  })

  it('changes with the mix spec', () => {
    expect(audioKey(timeline({ specDigest: 'spec2' }))).not.toBe(audioKey(timeline()))
  })

  it('changes when a song moves in the mix or starts elsewhere in the file', () => {
    const moved = timeline()
    moved.tracks = moved.tracks.map((t, i) => (i === 1 ? { ...t, mixStartSec: 300 } : t))
    const reStarted = timeline()
    reStarted.tracks = reStarted.tracks.map((t, i) => (i === 0 ? { ...t, startSec: 11 } : t))
    expect(audioKey(moved)).not.toBe(audioKey(timeline()))
    expect(audioKey(reStarted)).not.toBe(audioKey(timeline()))
  })

  it('changes when a song is dropped', () => {
    const fewer = timeline()
    fewer.tracks = fewer.tracks.slice(0, 1)
    expect(audioKey(fewer)).not.toBe(audioKey(timeline()))
  })
})
