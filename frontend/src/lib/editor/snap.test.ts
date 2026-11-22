import { describe, expect, it } from 'vitest'
import { snapCandidates } from './snap'
import { timelineOf } from './editorFixtures'

describe('snapCandidates', () => {
  const timeline = timelineOf()

  it('offers the nearest downbeats of the piece under the edge, nearest first, with the source position', () => {
    // 120 bpm: a 2 s bar; the first piece's downbeats are 1, 3, 5 ... s in class time.
    const found = snapCandidates(timeline, 60.4)
    expect(found[0]).toMatchObject({ piece: 0, classSec: 61, kind: 'downbeat' })
    expect(found[0]?.deltaSec).toBeCloseTo(0.6, 9)
    expect(found).toHaveLength(3)
    expect(found.map((f) => f.classSec)).toEqual([61, 59, 63])
    // the cut starts 10 s into the file: class 61 s is 61 s into the clip, 71 s into the file
    expect(found[0]?.sourceMs).toBe(71_000)
  })

  it('uses the incoming piece inside a crossfade', () => {
    expect(snapCandidates(timeline, 184)[0]?.piece).toBe(1)
  })

  it('offers nothing without a downbeat or a tempo', () => {
    const bare = timelineOf({ tracks: timeline.tracks.map((t) => ({ ...t, firstDownbeatSec: undefined })) })
    expect(snapCandidates(bare, 60)).toEqual([])
    expect(snapCandidates(timelineOf({ tracks: timeline.tracks.map((t) => ({ ...t, bpm: 0 })) }), 60)).toEqual([])
    expect(snapCandidates(timelineOf({ tracks: [] }), 60)).toEqual([])
  })
})
