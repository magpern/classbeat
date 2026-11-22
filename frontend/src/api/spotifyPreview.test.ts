import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeApi, json, reason } from '../test/fakeApi'
import { auditionPiece, clearCorrection, failureOf, pausePlayer, readAlignment, readPlayerState, setCorrection } from './spotifyPreview'
import { ApiError } from './client'

// The Spotify preview client: the proxy's shapes are decoded strictly, a failure is a value with a
// reason code (never thrown at the page, never a provider message), and no id of Spotify's is sent.

const BASE = '/api/classes/c1/preview/spotify'
const SONG = {
  piece: 0,
  song: 1,
  title: 'A',
  artist: 'B',
  length_ms: 200_000,
  analysis: { spotdown_job: 'job-1', sha256: 'ab'.repeat(32), timing_class: 'approximate' },
  alignment: { offset_ms: 1250, source: 'spotdown' },
  correction: null,
}
const ALIGNMENT = { revision_id: 'r1', map_version: 3, crossfade_ms: 9000, songs: [SONG] }
const STATE = { status: 'playing', track_id: 'x', pieces: [0], progress_ms: 5000, is_playing: true, timestamp: 1_700_000_000_000, device: { name: 'Kitchen', type: 'Speaker' }, round_trip_ms: 80 }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the player proxy', () => {
  it('plays by piece and position only: no track, uri or device is sent', async () => {
    const api = fakeApi({ [`POST ${BASE}/audition`]: { status: 'starting', piece_index: 2, track_id: 't', position_ms: 4000, device: { name: 'Kitchen', type: 'Speaker' }, round_trip_ms: 90 } })
    const result = await auditionPiece('c1', 2, 4000)
    expect(result).toEqual({ ok: true, value: { pieceIndex: 2, device: { name: 'Kitchen', type: 'Speaker' } } })
    expect(api.to('POST', `${BASE}/audition`)[0]?.body).toEqual({ piece_index: 2, position_ms: 4000 })
  })

  it('reads the observed state', async () => {
    fakeApi({ [`GET ${BASE}/state`]: STATE })
    expect(await readPlayerState('c1')).toEqual({
      ok: true,
      value: { status: 'playing', pieces: [0], progressMs: 5000, timestamp: 1_700_000_000_000, device: { name: 'Kitchen', type: 'Speaker' } },
    })
  })

  it('reads an idle state with no device and no position', async () => {
    fakeApi({ [`GET ${BASE}/state`]: { status: 'idle', pieces: [], progress_ms: null, is_playing: false, timestamp: null, device: null, round_trip_ms: 1 } })
    expect(await readPlayerState('c1')).toMatchObject({ ok: true, value: { status: 'idle', progressMs: null, device: null } })
  })

  it('pause answers the observed state', async () => {
    fakeApi({ [`POST ${BASE}/pause`]: { ...STATE, status: 'paused', is_playing: false, paused: true } })
    expect(await pausePlayer('c1')).toMatchObject({ ok: true, value: { status: 'paused' } })
  })

  it.each([
    [{ ...STATE, status: 'dancing' }],
    [{ ...STATE, pieces: [-1] }],
    [{ ...STATE, pieces: 'x' }],
    [{ ...STATE, progress_ms: 'soon' }],
    [{ ...STATE, device: { name: 1 } }],
    ['not an object'],
  ])('an answer of the wrong shape is invalid_response, not a state', async (body) => {
    fakeApi({ [`GET ${BASE}/state`]: body })
    expect(await readPlayerState('c1')).toEqual({ ok: false, failure: { reason: 'invalid_response', status: 502, retryAfterS: null } })
  })
})

describe('failures', () => {
  it('keeps the reason code and the status of a refusal', async () => {
    fakeApi({ [`POST ${BASE}/audition`]: reason(409, 'premium_or_allowlist') })
    expect(await auditionPiece('c1', 0, 0)).toEqual({ ok: false, failure: { reason: 'premium_or_allowlist', status: 409, retryAfterS: null } })
  })

  it('carries a 429 Retry-After from the header, else from the detail', async () => {
    fakeApi({ [`GET ${BASE}/state`]: () => json({ detail: { reason: 'rate_limited', retry_after: 12 } }, 429, { 'Retry-After': '9' }) })
    expect(await readPlayerState('c1')).toMatchObject({ failure: { reason: 'rate_limited', status: 429, retryAfterS: 9 } })
    fakeApi({ [`GET ${BASE}/state`]: () => json({ detail: { reason: 'rate_limited', retry_after: 12 } }, 429) })
    expect(await readPlayerState('c1')).toMatchObject({ failure: { retryAfterS: 12 } })
  })

  it('a 401 is "unauthorized" and a 403 with no code keeps its status for the sentence', () => {
    expect(failureOf(new ApiError(401, null, null, undefined))).toEqual({ reason: 'unauthorized', status: 401, retryAfterS: null })
    expect(failureOf(new ApiError(403, null, null, undefined))).toEqual({ reason: 'unknown', status: 403, retryAfterS: null })
  })

  it('a fetch that cannot reach the server is "network"; anything else that throws is "unknown", never a success', () => {
    expect(failureOf(new TypeError('Failed to fetch'))).toEqual({ reason: 'network', status: 0, retryAfterS: null })
    expect(failureOf(new Error('bug'))).toEqual({ reason: 'unknown', status: 0, retryAfterS: null })
  })

  it('a rejected fetch becomes a failure value, not an exception', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    expect(await readPlayerState('c1')).toEqual({ ok: false, failure: { reason: 'network', status: 0, retryAfterS: null } })
  })
})

describe('alignment and corrections', () => {
  it('reads each song with the analysis, the alignment and the correction apart', async () => {
    fakeApi({
      [`GET ${BASE}/alignment`]: {
        ...ALIGNMENT,
        songs: [{ ...SONG, correction: { offset_ms: -300, set_against_analysis: { map_version: 2, sha256: 'cd'.repeat(32) }, state: 'listen_again' } }],
      },
    })
    const result = await readAlignment('c1')
    expect(result).toMatchObject({ ok: true, value: { revisionId: 'r1', mapVersion: 3, crossfadeMs: 9000 } })
    const song = result.ok ? result.value.songs[0] : undefined
    expect(song?.alignmentOffsetMs).toBe(1250)
    expect(song?.analysis).toEqual({ spotdownJob: 'job-1', sha256: 'ab'.repeat(32), timingClass: 'approximate' })
    expect(song?.correction).toEqual({ offsetMs: -300, setAgainst: { mapVersion: 2, sha256: 'cd'.repeat(32) }, state: 'listen_again' })
  })

  it('a song with no alignment keeps null, not zero', async () => {
    fakeApi({ [`GET ${BASE}/alignment`]: { ...ALIGNMENT, songs: [{ ...SONG, alignment: { offset_ms: null, source: 'spotdown' } }] } })
    const result = await readAlignment('c1')
    expect(result.ok && result.value.songs[0]?.alignmentOffsetMs).toBeNull()
  })

  it('refuses a correction state it does not know', async () => {
    fakeApi({ [`GET ${BASE}/alignment`]: { ...ALIGNMENT, songs: [{ ...SONG, correction: { offset_ms: 5, set_against_analysis: null, state: 'applied' } }] } })
    expect(await readAlignment('c1')).toMatchObject({ ok: false, failure: { reason: 'invalid_response' } })
  })

  it('not a Spotify draft is a failure with its reason', async () => {
    fakeApi({ [`GET ${BASE}/alignment`]: reason(409, 'no_draft') })
    expect(await readAlignment('c1')).toMatchObject({ ok: false, failure: { reason: 'no_draft', status: 409 } })
  })

  it('a correction is sent with the draft it applies to and nothing else', async () => {
    const api = fakeApi({ [`PUT ${BASE}/corrections/2`]: ALIGNMENT })
    expect(await setCorrection('c1', 2, 'r1', -500)).toMatchObject({ ok: true })
    expect(api.to('PUT', `${BASE}/corrections/2`)[0]?.body).toEqual({ revision_id: 'r1', offset_ms: -500 })
  })

  it('clearing names the draft in the query', async () => {
    const api = fakeApi({ [`DELETE ${BASE}/corrections/2?revision_id=r1`]: ALIGNMENT })
    expect(await clearCorrection('c1', 2, 'r1')).toMatchObject({ ok: true })
    expect(api.calls.at(-1)?.path).toBe(`${BASE}/corrections/2?revision_id=r1`)
  })

  it('a stale draft is a failure the page can name', async () => {
    fakeApi({ [`PUT ${BASE}/corrections/1`]: reason(409, 'stale_base') })
    expect(await setCorrection('c1', 1, 'old', 100)).toMatchObject({ ok: false, failure: { reason: 'stale_base' } })
  })
})
