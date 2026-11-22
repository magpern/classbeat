import { describe, expect, it } from 'vitest'
import {
  NO_PROVENANCE,
  buildSpotifyModel,
  lanesAt,
  mapStudio,
  spotifyCursor,
  studioClassMs,
  studioGain,
  type SpotifySong,
  type StudioClip,
} from './timeModel'

const clips: StudioClip[] = [
  { piece: 0, label: 'One', startMs: 0, endMs: 100_000, fadeInMs: 0, fadeOutMs: 8_000, sourceStartMs: 0 },
  { piece: 1, label: 'Two', startMs: 92_000, endMs: 200_000, fadeInMs: 8_000, fadeOutMs: 0, sourceStartMs: 30_000 },
]

describe('studio mapping', () => {
  it('maps a body instant to one piece with transportMs = classMs - clip.start', () => {
    const [only, ...rest] = mapStudio(clips, 50_000)
    expect(rest).toEqual([])
    expect(only).toMatchObject({ piece: 0, transportMs: 50_000, sourceMs: 50_000, gain: 1, role: 'solo' })
    const [two] = mapStudio(clips, 150_000)
    expect(two).toMatchObject({ piece: 1, transportMs: 58_000, sourceMs: 88_000, role: 'solo' })
  })

  it('maps a crossfade instant to BOTH pieces, incoming and outgoing', () => {
    const both = mapStudio(clips, 96_000)
    expect(both.map((m) => [m.piece, m.role])).toEqual([
      [0, 'outgoing'],
      [1, 'incoming'],
    ])
    expect(both[0]?.transportMs).toBe(96_000)
    expect(both[1]?.transportMs).toBe(4_000)
  })

  it('uses the equal-power curves: gains are sqrt(1/2) at mid fade and the squares sum to 1', () => {
    const [out, inc] = mapStudio(clips, 96_000)
    expect(out?.gain).toBeCloseTo(Math.SQRT1_2, 9)
    expect(inc?.gain).toBeCloseTo(Math.SQRT1_2, 9)
    for (const t of [92_000, 93_500, 97_000, 99_999]) {
      const [a, b] = mapStudio(clips, t)
      expect((a?.gain ?? 0) ** 2 + (b?.gain ?? 0) ** 2).toBeCloseTo(1, 9)
    }
  })

  it('has gain 0 at the very start of an incoming fade and 1 right after it', () => {
    const second = clips[1] as StudioClip
    expect(studioGain(second, 92_000)).toBeCloseTo(0, 9)
    expect(studioGain(second, 100_000)).toBe(1)
  })

  it('maps nothing outside the class and treats the end as exclusive', () => {
    expect(mapStudio(clips, -1)).toEqual([])
    expect(mapStudio(clips, 200_000)).toEqual([])
    expect(mapStudio(clips, 199_999)).toHaveLength(1)
  })

  it('leaves sourceMs and provenance as not recorded when the API does not say', () => {
    const bare: StudioClip = { ...(clips[0] as StudioClip), sourceStartMs: null }
    const [m] = mapStudio([bare], 10)
    expect(m?.sourceMs).toBeNull()
    expect(m?.provenance).toEqual(NO_PROVENANCE)
  })

  it('carries a clip provenance through, unmerged', () => {
    const provenance = {
      analysis: { mapVersion: 'v3', spotdownJob: 'job-1', timingClass: 'usable' },
      alignment: { offsetMs: 120, source: 'spotdown' },
      correction: { offsetMs: -20, setAgainstAnalysis: 'v3' },
    }
    const [m] = mapStudio([{ ...(clips[0] as StudioClip), provenance }], 10)
    expect(m?.provenance).toEqual(provenance)
  })

  it('finds a class instant from a piece and a transport position', () => {
    expect(studioClassMs(clips, 1, 4_000)).toBe(96_000)
    expect(studioClassMs(clips, 9, 4_000)).toBeNull()
  })
})

const song = (piece: number, lengthMs: number, extra: Partial<SpotifySong> = {}): SpotifySong => ({
  piece,
  label: `S${piece}`,
  lengthMs,
  alignmentOffsetMs: 0,
  correctionOffsetMs: 0,
  timingClass: 'usable',
  ...extra,
})

describe('Spotify overlap model', () => {
  const songs = [song(0, 200_000), song(1, 180_000), song(2, 160_000)]

  it('places S_{i+1} = E_i - C with C read from the input, not a constant', () => {
    for (const c of [9_000, 9_540, 0]) {
      const model = buildSpotifyModel(songs, c)
      expect(model.crossfadeMs).toBe(c)
      expect(model.windows.map((w) => w.startMs)).toEqual([0, 200_000 - c, 200_000 - c + 180_000 - c])
      for (const [i, w] of model.windows.entries()) expect(w.endMs - w.startMs).toBe(songs[i]?.lengthMs)
    }
  })

  it('has overlap regions [S_{i+1}, E_i) of length C, and none when C is 0', () => {
    const model = buildSpotifyModel(songs, 9_540)
    expect(model.overlaps).toEqual([
      { outgoing: 0, incoming: 1, startMs: 190_460, endMs: 200_000 },
      { outgoing: 1, incoming: 2, startMs: 190_460 + 180_000 - 9_540, endMs: 190_460 + 180_000 },
    ])
    expect(buildSpotifyModel(songs, 0).overlaps).toEqual([])
  })

  it('offers one lane outside an overlap and two inside, the incoming song the default', () => {
    const model = buildSpotifyModel(songs, 9_000)
    expect(lanesAt(model, 100_000)).toEqual([{ piece: 0, sourceMs: 100_000, role: 'only', isDefault: true }])
    const lanes = lanesAt(model, 195_000)
    expect(lanes).toEqual([
      { piece: 1, sourceMs: 195_000 - 191_000, role: 'incoming', isDefault: true },
      { piece: 0, sourceMs: 195_000, role: 'outgoing', isDefault: false },
    ])
    expect(lanesAt(model, 191_000)[0]?.piece).toBe(1)
    expect(lanesAt(model, 200_000).map((l) => l.piece)).toEqual([1])
    expect(lanesAt(model, 10_000_000)).toEqual([])
  })

  describe('the cursor from an observed position', () => {
    const model = buildSpotifyModel(
      [song(0, 200_000, { alignmentOffsetMs: 500, correctionOffsetMs: 100 }), song(1, 180_000, { timingClass: 'unusable' }), song(2, 100_000, { alignmentOffsetMs: null })],
      9_000,
    )

    it('is external when the playing track is not the requested one', () => {
      expect(spotifyCursor(model, 0, { requestedTrack: false, transportMs: 1_000 })).toEqual({ status: 'external' })
    })

    it('is unaligned for an unusable timing class or no alignment', () => {
      expect(spotifyCursor(model, 1, { requestedTrack: true, transportMs: 1_000 })).toEqual({ status: 'unaligned' })
      expect(spotifyCursor(model, 2, { requestedTrack: true, transportMs: 1_000 })).toEqual({ status: 'unaligned' })
    })

    it('subtracts the alignment and the correction separately and adds S_i', () => {
      expect(spotifyCursor(model, 0, { requestedTrack: true, transportMs: 50_600 })).toEqual({ status: 'playing', classMs: 50_000, sourceMs: 50_000, inOverlap: false })
    })

    it('marks a cursor inside an overlap', () => {
      const c = spotifyCursor(model, 0, { requestedTrack: true, transportMs: 195_600 })
      expect(c).toMatchObject({ status: 'playing', inOverlap: true })
    })

    it('is outside_window before and after the song window', () => {
      expect(spotifyCursor(model, 0, { requestedTrack: true, transportMs: 100 })).toMatchObject({ status: 'outside_window' })
      expect(spotifyCursor(model, 0, { requestedTrack: true, transportMs: 200_600 })).toMatchObject({ status: 'outside_window' })
    })

    it('is external for a piece that is not in the model', () => {
      expect(spotifyCursor(model, 9, { requestedTrack: true, transportMs: 1 })).toEqual({ status: 'external' })
    })
  })
})
