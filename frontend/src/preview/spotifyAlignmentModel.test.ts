import { describe, expect, it } from 'vitest'
import type { Alignment, AlignmentSong } from '../api/spotifyPreview'
import { appliedCorrectionMs, modelOf, MAX_CORRECTION_MS, NUDGE_STEPS_MS, nudged, provenanceOf, setAgainstLabel, spotifySongOf, transportPositionMs } from './spotifyAlignmentModel'
import { spotifyCursor } from './timeModel'

const SHA = 'ab'.repeat(32)
const song = (piece: number, extra: Partial<AlignmentSong> = {}): AlignmentSong => ({
  piece,
  song: piece + 1,
  title: `Song ${String(piece + 1)}`,
  artist: 'X',
  lengthMs: 200_000,
  analysis: { spotdownJob: `job-${String(piece)}`, sha256: SHA, timingClass: 'approximate' },
  alignmentOffsetMs: 1000,
  correction: null,
  ...extra,
})
const correction = (offsetMs: number, state: 'current' | 'listen_again' = 'current') => ({
  offsetMs,
  setAgainst: { mapVersion: 3, sha256: SHA },
  state,
})

describe('the analysis fact and the correction are never merged', () => {
  it('the model keeps spotdown’s alignment and the correction as two numbers', () => {
    const s = spotifySongOf(song(0, { correction: correction(-250) }), 3)
    expect(s.alignmentOffsetMs).toBe(1000)
    expect(s.correctionOffsetMs).toBe(-250)
  })

  it('the provenance carries both, each its own', () => {
    const p = provenanceOf(song(0, { correction: correction(-250) }), 3)
    expect(p.alignment).toEqual({ offsetMs: 1000, source: 'spotdown' })
    expect(p.correction).toEqual({ offsetMs: -250, setAgainstAnalysis: 'v3 abababababab' })
    expect(p.analysis).toEqual({ mapVersion: '3', spotdownJob: 'job-0', timingClass: 'approximate' })
  })

  it('a song with no correction has none, and no alignment is "not recorded", not 0', () => {
    const p = provenanceOf(song(0, { alignmentOffsetMs: null }), null)
    expect(p.correction).toBeNull()
    expect(p.alignment).toEqual({ offsetMs: null, source: null })
    expect(p.analysis.mapVersion).toBeNull()
  })

  it('the cursor subtracts each on its own', () => {
    const model = modelOf({ revisionId: 'r', mapVersion: 3, crossfadeMs: 0, songs: [song(0, { correction: correction(-250) })] })
    const cursor = spotifyCursor(model, 0, { requestedTrack: true, transportMs: 11_000 })
    // sourceMs = 11000 - 1000 (spotdown) - (-250) (the correction)
    expect(cursor).toMatchObject({ status: 'playing', sourceMs: 10_250 })
  })
})

describe('listen again', () => {
  it('a correction set against another analysis is shown but not applied', () => {
    const stale = song(0, { correction: correction(700, 'listen_again') })
    expect(appliedCorrectionMs(stale)).toBe(0)
    expect(spotifySongOf(stale, 3).correctionOffsetMs).toBe(0)
    expect(provenanceOf(stale, 3).correction?.offsetMs).toBe(700) // still shown
  })

  it('a current one is applied', () => {
    expect(appliedCorrectionMs(song(0, { correction: correction(700) }))).toBe(700)
    expect(appliedCorrectionMs(song(0))).toBe(0)
  })
})

describe('the class model', () => {
  it('lays the songs out with the crossfade constant of the class map', () => {
    const alignment: Alignment = { revisionId: 'r', mapVersion: 3, crossfadeMs: 9000, songs: [song(0), song(1), song(2)] }
    const model = modelOf(alignment)
    expect(model.crossfadeMs).toBe(9000)
    expect(model.windows.map((w) => [w.startMs, w.endMs])).toEqual([[0, 200_000], [191_000, 391_000], [382_000, 582_000]])
    expect(model.overlaps).toHaveLength(2)
  })

  it('a position to play is the inverse of the cursor mapping', () => {
    const s = spotifySongOf(song(0, { alignmentOffsetMs: -500, correction: correction(300) }), 3)
    const model = modelOf({ revisionId: 'r', mapVersion: 3, crossfadeMs: 0, songs: [song(0, { alignmentOffsetMs: -500, correction: correction(300) })] })
    const at = transportPositionMs(s, 42_000)
    expect(at).toBe(42_000 - 500 + 300)
    expect(spotifyCursor(model, 0, { requestedTrack: true, transportMs: at })).toMatchObject({ sourceMs: 42_000 })
  })

  it('never asks for a negative position', () => {
    expect(transportPositionMs(spotifySongOf(song(0, { alignmentOffsetMs: -9000 }), 3), 100)).toBe(0)
    expect(transportPositionMs(spotifySongOf(song(0, { alignmentOffsetMs: null }), 3), 5000)).toBe(5000)
  })
})

describe('nudging', () => {
  it('the steps are 0.1, 0.5, 1, 5 and 10 s', () => {
    expect(NUDGE_STEPS_MS).toEqual([100, 500, 1000, 5000, 10_000])
  })

  it('adds, clamps to a minute either way, and clears when it comes to zero', () => {
    expect(nudged(0, 500)).toBe(500)
    expect(nudged(500, -500)).toBeNull()
    expect(nudged(-100, 100)).toBeNull()
    expect(nudged(55_000, 10_000)).toBe(MAX_CORRECTION_MS)
    expect(nudged(-55_000, -10_000)).toBe(-MAX_CORRECTION_MS)
  })

  it('names what a correction was set against without a Spotify id', () => {
    expect(setAgainstLabel({ mapVersion: null, sha256: SHA })).toBe('- abababababab')
    expect(setAgainstLabel({ mapVersion: 4, sha256: SHA })).toBe('v4 abababababab')
  })
})
