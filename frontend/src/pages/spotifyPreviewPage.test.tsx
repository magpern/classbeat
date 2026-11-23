import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CLASS_PATH, classBody } from '../test/classFixtures'
import { EDIT_PAGE, editorRoutes, refusal } from '../test/editorApi'
import { fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'

// The Spotify listening preview on the read-only editor page (ADR 0042 section 7, CP-S3): Play only
// by a click, the cursor only from what the player was observed to do, every failure in words, the
// lane choice in an overlap, and the timing corrections. The player and the backend are fakes.

const A = `${CLASS_PATH}/preview/spotify`
const OVERLAY = `GET ${CLASS_PATH}/overlay`
const SHA = 'ab'.repeat(32)
const DEVICE = { name: 'Kitchen', type: 'Speaker' }

function song(piece: number, extra: Record<string, unknown> = {}) {
  return {
    piece,
    song: piece + 1,
    title: `Song ${String(piece + 1)}`,
    artist: 'Artist',
    length_ms: 200_000,
    analysis: { spotdown_job: 'job-1', sha256: SHA, timing_class: 'good' },
    alignment: { offset_ms: 0, source: 'spotdown' },
    correction: null,
    ...extra,
  }
}

// Three songs of 200 s with a 10 s crossfade: windows 0-200, 190-390 and 380-580 s.
const alignment = (songs = [song(0), song(1), song(2)]) => ({ revision_id: 'r1', map_version: 3, crossfade_ms: 10_000, songs })
const correction = (offset: number, state = 'current') => ({ offset_ms: offset, set_against_analysis: { map_version: 3, sha256: SHA }, state })
const status = (granted: boolean) => ({
  status: 'connected',
  client_id: null,
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
  pool_consent: false,
  consent_version: null,
  consent_text: 'x',
  preview_granted: granted,
})
const playing = (piece: number, progress: number) => ({ status: 'playing', pieces: [piece], progress_ms: progress, timestamp: Date.now(), device: DEVICE, round_trip_ms: 80 })

function routes(extra: Record<string, unknown> = {}, granted = true) {
  return editorRoutes({
    [`GET ${CLASS_PATH}`]: classBody({ spotify: true }), // a Spotify class, not the studio's
    [OVERLAY]: () => refusal('edit_unavailable'),
    'GET /api/connections/spotify': status(granted),
    [`GET ${A}/alignment`]: alignment(),
    ...extra,
  })
}

const reasonOf = (code: string, http: number, headers: Record<string, string> = {}) => () => json({ detail: { reason: code } }, http, headers)

async function open() {
  renderAt(EDIT_PAGE)
  return screen.findByTestId('spotify-audition')
}

const press = (name: RegExp | string) => fireEvent.click(screen.getByRole('button', { name }))
const flushed = (fn: () => void) => act(() => { fn(); return Promise.resolve() })
const settle = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms))

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => {
  vi.useRealTimers()
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})

describe('opening the page', () => {
  it('shows the songs and Play beside the read-only notice, and starts nothing by itself', async () => {
    const api = fakeApi(routes())
    await open()
    expect(screen.getByTestId('readonly-edit_unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled()
    expect(screen.getByRole('button', { name: /^Song 2: Song 2/ })).toBeInTheDocument()
    await settle(5000)
    expect(api.to('POST', `${A}/audition`)).toHaveLength(0)
    expect(api.to('GET', `${A}/state`)).toHaveLength(0) // nothing is polled until a play
    expect(screen.queryByTestId('spotify-cursor-line')).toBeNull()
    expect(document.querySelector('audio, video')).toBeNull()
    expect(screen.getByTestId('spotify-status')).toHaveTextContent(/Nothing is playing from here/)
    // Editing stays unavailable: no editor toolbar or handles.
    expect(screen.queryByTestId('editor-toolbar')).toBeNull()
    expect(screen.queryByRole('slider')).toBeNull()
  })

  it('shows nothing extra for a draft that is not a Spotify class', async () => {
    fakeApi(routes({ [`GET ${A}/alignment`]: reasonOf('no_draft', 409) }))
    renderAt(EDIT_PAGE)
    expect(await screen.findByTestId('readonly-edit_unavailable')).toBeInTheDocument()
    await waitFor(() => { expect(screen.queryByTestId('spotify-loading')).toBeNull() })
    expect(screen.queryByTestId('spotify-audition')).toBeNull()
  })

  it('says when the preview could not be loaded, and tries again', async () => {
    let fail = true
    const api = fakeApi(routes({ [`GET ${A}/alignment`]: () => (fail ? json({ detail: { reason: 'upstream_unavailable' } }, 502) : alignment()) }))
    renderAt(EDIT_PAGE)
    expect(await screen.findByTestId('spotify-load-failed')).toHaveTextContent(/could not be loaded/)
    fail = false
    press('Try again')
    await screen.findByTestId('spotify-audition')
    expect(api.to('GET', `${A}/alignment`)).toHaveLength(2)
  })

  it('still opens, and lets the backend decide at Play, when the connection status cannot be read', async () => {
    fakeApi(routes({ 'GET /api/connections/spotify': () => json({ detail: { reason: 'upstream_unavailable' } }, 500) }))
    await open()
    expect(screen.queryByTestId('spotify-not-granted')).toBeNull()
    expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled()
  })

  it('asks to reconnect, in words, with a link to the Spotify connection, when playback control was not allowed', async () => {
    const api = fakeApi(routes({}, false))
    await open()
    const box = screen.getByTestId('spotify-not-granted')
    expect(box).toHaveTextContent(/not switched on for your Spotify connection/)
    expect(within(box).getByRole('link', { name: 'Reconnect Spotify' }).getAttribute('href')).toContain('/account/spotify')
    expect(screen.getByRole('button', { name: 'Play' })).toBeDisabled()
    expect(api.to('POST', `${A}/audition`)).toHaveLength(0)
  })
})

describe('playing, and the cursor', () => {
  it('plays on a click, and draws the cursor only from the state the player reports', async () => {
    let seen: unknown = { status: 'idle', pieces: [], progress_ms: null, timestamp: null, device: null }
    const api = fakeApi(
      routes({
        [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE, round_trip_ms: 70 },
        [`GET ${A}/state`]: () => seen,
      }),
    )
    await open()
    press('Play')
    await waitFor(() => { expect(api.to('POST', `${A}/audition`)).toHaveLength(1) })
    expect(api.to('POST', `${A}/audition`)[0]?.body).toEqual({ piece_index: 0, position_ms: 0 })
    // Accepted, not yet observed: starting, and no cursor (the requested position is never drawn).
    expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'starting')
    expect(screen.queryByTestId('spotify-cursor-line')).toBeNull()
    seen = playing(0, 61_000)
    await settle(1100)
    expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'playing')
    expect(screen.getByTestId('spotify-cursor')).toHaveTextContent(/^Cursor at 1:0\d, about ±\d+ ms\.$/)
    expect(screen.getByTestId('spotify-cursor-line')).toBeInTheDocument()
    expect(screen.getByTestId('spotify-band')).toHaveAttribute('data-overlap', 'false')
  })

  it('has no cursor when something else plays, even after a play was accepted', async () => {
    fakeApi(
      routes({
        [`POST ${A}/audition`]: { status: 'starting', piece_index: 1, device: DEVICE },
        [`GET ${A}/state`]: { status: 'external', pieces: [], progress_ms: 31_000, timestamp: Date.now(), device: DEVICE },
      }),
    )
    await open()
    fireEvent.click(screen.getByTestId('spotify-song-1'))
    await settle(1100)
    await settle(7000) // past the starting grace
    expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'external')
    expect(screen.getByTestId('spotify-status')).toHaveTextContent(/Something else is playing/)
    expect(screen.queryByTestId('spotify-cursor-line')).toBeNull()
  })

  it('is unaligned for a song whose timing is unusable: it plays, with no cursor', async () => {
    fakeApi(
      routes({
        [`GET ${A}/alignment`]: alignment([song(0, { analysis: { spotdown_job: 'j', sha256: SHA, timing_class: 'unusable' } }), song(1), song(2)]),
        [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE },
        [`GET ${A}/state`]: () => playing(0, 20_000),
      }),
    )
    await open()
    press('Play')
    await settle(1100)
    expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'unaligned')
    expect(screen.getByTestId('spotify-status')).toHaveTextContent(/not reliable/)
    expect(screen.queryByTestId('spotify-cursor-line')).toBeNull()
  })

  it('is outside the window in an intro: no cursor yet', async () => {
    fakeApi(
      routes({
        [`GET ${A}/alignment`]: alignment([song(0, { alignment: { offset_ms: 15_000, source: 'spotdown' } }), song(1), song(2)]),
        [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE },
        [`GET ${A}/state`]: () => playing(0, 4_000),
      }),
    )
    await open()
    press('Play')
    await settle(1100)
    expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'outside_window')
    expect(screen.getByTestId('spotify-status')).toHaveTextContent(/before this class starts/)
    expect(screen.queryByTestId('spotify-cursor-line')).toBeNull()
  })

  it('pauses on a click and reports the paused state it observed', async () => {
    let seen: unknown = playing(0, 10_000)
    const api = fakeApi(
      routes({
        [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE },
        [`GET ${A}/state`]: () => seen,
        [`POST ${A}/pause`]: () => ({ status: 'paused', pieces: [0], progress_ms: 12_000, timestamp: Date.now(), device: DEVICE, paused: true }),
      }),
    )
    await open()
    expect(screen.getByRole('button', { name: 'Pause' })).toBeDisabled()
    press('Play')
    await settle(1100)
    seen = { ...playing(0, 12_000), status: 'paused' }
    press('Pause')
    await waitFor(() => { expect(api.to('POST', `${A}/pause`)).toHaveLength(1) })
    await waitFor(() => { expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'paused') })
    expect(screen.getByTestId('spotify-status')).toHaveTextContent(/Paused on Kitchen/)
  })

  it('notes a seek (the position jumped) and a device switch, and follows the player', async () => {
    let seen: unknown = playing(0, 10_000)
    fakeApi(routes({ [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE }, [`GET ${A}/state`]: () => seen }))
    await open()
    press('Play')
    await settle(1100)
    seen = playing(0, 90_000)
    await settle(1100)
    expect(screen.getByTestId('spotify-note')).toHaveTextContent(/position jumped/)
    seen = { ...playing(0, 91_500), device: { name: 'Phone', type: 'Smartphone' } }
    await settle(1100)
    expect(screen.getByTestId('spotify-note')).toHaveTextContent(/Now playing on Phone/)
  })
})

describe('an overlap: the person chooses the song', () => {
  async function inOverlap() {
    const api = fakeApi(routes({ [`POST ${A}/audition`]: { status: 'starting', piece_index: 1, device: DEVICE } }))
    await open()
    fireEvent.click(screen.getByTestId('spotify-overlap-0'))
    const picker = await screen.findByTestId('lane-picker')
    return { api, picker }
  }

  it('offers the incoming song (the default) and the outgoing one, and plays nothing until chosen', async () => {
    const { api, picker } = await inOverlap()
    expect(within(picker).getByTestId('lane-incoming')).toHaveTextContent('The incoming song (Song 2)')
    expect(within(picker).getByTestId('lane-outgoing')).toHaveTextContent('The outgoing song (Song 1)')
    expect(api.to('POST', `${A}/audition`)).toHaveLength(0)
  })

  it('plays the incoming song from its own position when chosen', async () => {
    const { api } = await inOverlap()
    fireEvent.click(screen.getByTestId('lane-incoming'))
    await waitFor(() => { expect(api.to('POST', `${A}/audition`)).toHaveLength(1) })
    expect(api.to('POST', `${A}/audition`)[0]?.body).toEqual({ piece_index: 1, position_ms: 0 })
    expect(screen.queryByTestId('lane-picker')).toBeNull()
  })

  it('plays the outgoing song from where the overlap starts in it', async () => {
    const { api } = await inOverlap()
    fireEvent.click(screen.getByTestId('lane-outgoing'))
    await waitFor(() => { expect(api.to('POST', `${A}/audition`)).toHaveLength(1) })
    expect(api.to('POST', `${A}/audition`)[0]?.body).toEqual({ piece_index: 0, position_ms: 190_000 })
  })

  it('cancels without playing', async () => {
    const { api } = await inOverlap()
    press('Cancel')
    expect(screen.queryByTestId('lane-picker')).toBeNull()
    expect(api.to('POST', `${A}/audition`)).toHaveLength(0)
  })

  it('labels the band as approximate while the cursor is inside the overlap', async () => {
    fakeApi(routes({ [`POST ${A}/audition`]: { status: 'starting', piece_index: 1, device: DEVICE }, [`GET ${A}/state`]: () => playing(1, 5_000) }))
    await open()
    fireEvent.click(screen.getByTestId('spotify-song-1'))
    await settle(1100)
    expect(screen.getByTestId('spotify-cursor')).toHaveTextContent(/Inside an overlap of two songs: the cursor is only approximate/)
    expect(screen.getByTestId('spotify-band')).toHaveAttribute('data-overlap', 'true')
  })
})

describe('every failure is a sentence, never a code', () => {
  const cases: [string, number, string, RegExp][] = [
    ['premium_or_allowlist', 403, 'premium_or_allowlist', /needs Spotify Premium/],
    ['a bare 403 from Spotify', 403, 'forbidden', /needs Spotify Premium/],
    ['no_device', 409, 'no_device', /No Spotify app is open/],
    ['needs_reconnect', 409, 'needs_reconnect', /needs to be connected again/],
    ['preview_not_granted', 403, 'preview_not_granted', /not switched on for your Spotify connection/],
    ['upstream_unavailable', 502, 'upstream_unavailable', /not answering right now/],
    ['a code nobody knows', 500, 'weird_new_code', /Something went wrong/],
  ]
  for (const [name, http, code, sentence] of cases) {
    it(`${name}`, async () => {
      fakeApi(routes({ [`POST ${A}/audition`]: reasonOf(code, http) }))
      await open()
      press('Play')
      await waitFor(() => { expect(screen.getByTestId('spotify-status')).toHaveTextContent(sentence) })
      expect(screen.getByTestId('spotify-status')).not.toHaveTextContent(code)
      expect(screen.queryByTestId('spotify-cursor-line')).toBeNull()
    })
  }

  it('no_device keeps a way forward: open Spotify, then press Play again', async () => {
    fakeApi(routes({ [`POST ${A}/audition`]: reasonOf('no_device', 409) }))
    await open()
    press('Play')
    expect(await screen.findByText(/Open Spotify, then press Play again/)).toBeInTheDocument()
    expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'no_device')
  })

  it('needs_reconnect links to the Spotify connection', async () => {
    fakeApi(routes({ [`POST ${A}/audition`]: reasonOf('needs_reconnect', 409) }))
    await open()
    press('Play')
    const link = await within(screen.getByTestId('spotify-status')).findByRole('link', { name: 'Reconnect Spotify' })
    expect(link.getAttribute('href')).toContain('/account/spotify')
  })

  it('a 429 says when to try again and waits that long before it reads the player again', async () => {
    let limited = true
    const api = fakeApi(
      routes({
        [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE },
        [`GET ${A}/state`]: () => (limited ? json({ detail: { reason: 'rate_limited', retry_after: 12 } }, 429, { 'Retry-After': '12' }) : playing(0, 40_000)),
      }),
    )
    await open()
    press('Play')
    await settle(1100)
    expect(screen.getByTestId('spotify-status')).toHaveTextContent('Try again in 12 seconds.')
    const asked = api.to('GET', `${A}/state`).length
    await settle(8000)
    expect(api.to('GET', `${A}/state`)).toHaveLength(asked) // still inside the Retry-After
    limited = false
    await settle(5000)
    expect(api.to('GET', `${A}/state`).length).toBeGreaterThan(asked)
    await waitFor(() => { expect(screen.getByTestId('spotify-status')).toHaveAttribute('data-state', 'playing') })
  })
})

describe('polling', () => {
  it('stops while the page is hidden, and reads once at once when it is shown again', async () => {
    const api = fakeApi(routes({ [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE }, [`GET ${A}/state`]: () => playing(0, 30_000) }))
    await open()
    press('Play')
    await settle(2200)
    const asked = api.to('GET', `${A}/state`).length
    expect(asked).toBeGreaterThan(0)
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    await settle(6000)
    expect(api.to('GET', `${A}/state`)).toHaveLength(asked)
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    await settle(10)
    expect(api.to('GET', `${A}/state`)).toHaveLength(asked + 1)
  })

  it('stops for good when it leaves the page', async () => {
    const api = fakeApi(routes({ [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE }, [`GET ${A}/state`]: () => playing(0, 30_000) }))
    await open()
    press('Play')
    await settle(1100)
    act(() => { fireEvent.click(screen.getByRole('link', { name: 'Open the class' })) })
    await waitFor(() => { expect(screen.queryByTestId('spotify-audition')).toBeNull() })
    const asked = api.to('GET', `${A}/state`).length
    await settle(5000)
    expect(api.to('GET', `${A}/state`)).toHaveLength(asked)
  })
})

describe('timing corrections', () => {
  const PUT = `PUT ${A}/corrections/1`
  const DELETE = `DELETE ${A}/corrections/1?revision_id=r1`

  it('nudges by the step chosen, on the draft that was read, and shows the answer', async () => {
    const api = fakeApi(routes({ [PUT]: alignment([song(0, { correction: correction(1000) }), song(1), song(2)]) }))
    await open()
    expect(screen.getByTestId('correction-value')).toHaveTextContent('No correction.')
    fireEvent.click(screen.getByTestId('nudge-plus-1000'))
    await waitFor(() => { expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(1) })
    expect(api.to('PUT', `${A}/corrections/1`)[0]?.body).toEqual({ revision_id: 'r1', offset_ms: 1000 })
    await waitFor(() => { expect(screen.getByTestId('correction-value')).toHaveTextContent('Your correction: 1.0 s') })
  })

  it('has the five steps either way', async () => {
    fakeApi(routes())
    await open()
    for (const step of [100, 500, 1000, 5000, 10000]) {
      expect(screen.getByTestId(`nudge-plus-${String(step)}`)).toBeInTheDocument()
      expect(screen.getByTestId(`nudge-minus-${String(step)}`)).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: 'Move by −0.1 s' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move by +10 s' })).toBeInTheDocument()
  })

  it('a nudge back to zero clears the correction', async () => {
    const api = fakeApi(routes({ [`GET ${A}/alignment`]: alignment([song(0, { correction: correction(1000) }), song(1), song(2)]), [DELETE]: alignment() }))
    await open()
    fireEvent.click(screen.getByTestId('nudge-minus-1000'))
    await waitFor(() => { expect(api.to('DELETE', `${A}/corrections/1?revision_id=r1`)).toHaveLength(1) })
    expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(0)
    await waitFor(() => { expect(screen.getByTestId('correction-value')).toHaveTextContent('No correction.') })
  })

  it('sends one write at a time: the next waits for the answer', async () => {
    let answer: (value: unknown) => void = () => undefined
    const api = fakeApi(routes({ [PUT]: () => new Promise((resolve) => { answer = resolve }) }))
    await open()
    fireEvent.click(screen.getByTestId('nudge-plus-500'))
    await waitFor(() => { expect(screen.getByTestId('nudge-plus-500')).toBeDisabled() })
    fireEvent.click(screen.getByTestId('nudge-plus-500'))
    fireEvent.click(screen.getByTestId('nudge-plus-5000'))
    expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(1)
    await flushed(() => { answer(alignment([song(0, { correction: correction(500) }), song(1), song(2)])) })
    await waitFor(() => { expect(screen.getByTestId('nudge-plus-500')).toBeEnabled() })
  })

  it('a stale draft reads the class again and says so', async () => {
    const api = fakeApi(routes({ [PUT]: reasonOf('stale_base', 409) }))
    await open()
    fireEvent.click(screen.getByTestId('nudge-plus-100'))
    await waitFor(() => { expect(api.to('GET', `${A}/alignment`)).toHaveLength(2) })
  })

  it('a refused or failed write is a sentence and leaves the correction as it was', async () => {
    fakeApi(routes({ [PUT]: reasonOf('correction_limit', 409) }))
    await open()
    fireEvent.click(screen.getByTestId('nudge-plus-100'))
    expect(await screen.findByText(/too many timing corrections/)).toBeInTheDocument()
    expect(screen.getByTestId('correction-value')).toHaveTextContent('No correction.')
  })

  it('an answer that cannot be read is a sentence, and the correction stays as it was', async () => {
    fakeApi(routes({ [PUT]: { revision_id: 'r1' } }))
    await open()
    fireEvent.click(screen.getByTestId('nudge-plus-100'))
    expect(await screen.findByText(/answer that could not be used/)).toBeInTheDocument()
    expect(screen.getByTestId('correction-value')).toHaveTextContent('No correction.')
    expect(screen.getByTestId('nudge-plus-100')).toBeEnabled() // the write is over: another can be sent
  })

  it('keeps spotdown’s alignment and the correction apart, and never adds them', async () => {
    fakeApi(routes({ [`GET ${A}/alignment`]: alignment([song(0, { alignment: { offset_ms: 1250, source: 'spotdown' }, correction: correction(2000) }), song(1), song(2)]) }))
    await open()
    const prov = screen.getByTestId('spotify-provenance')
    expect(prov).toHaveTextContent('1250 ms')
    expect(prov).toHaveTextContent(/2000 ms \(v3 /)
    expect(prov).not.toHaveTextContent('3250')
  })

  it('applies a current correction to the cursor', async () => {
    fakeApi(
      routes({
        [`GET ${A}/alignment`]: alignment([song(0, { correction: correction(2000) }), song(1), song(2)]),
        [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE },
        [`GET ${A}/state`]: () => playing(0, 62_000),
      }),
    )
    await open()
    press('Play')
    await settle(1100)
    expect(screen.getByTestId('spotify-cursor')).toHaveTextContent(/^Cursor at 1:0\d/) // 62 s - 2 s = 60 s (+ the poll age)
  })

  describe('listen again', () => {
    const again = () => alignment([song(0, { correction: { offset_ms: 2000, set_against_analysis: { map_version: 2, sha256: 'cd'.repeat(32) }, state: 'listen_again' } }), song(1), song(2)])

    it('is shown, says why, and is not applied to the cursor', async () => {
      fakeApi(
        routes({
          [`GET ${A}/alignment`]: again(),
          [`POST ${A}/audition`]: { status: 'starting', piece_index: 0, device: DEVICE },
          [`GET ${A}/state`]: () => playing(0, 62_000),
        }),
      )
      await open()
      expect(screen.getByText(/Listen again: this correction was set before the music was analysed again/)).toBeInTheDocument()
      expect(screen.getByTestId('correction-value')).toHaveTextContent('Your correction: 2.0 s')
      press('Play')
      await settle(1100)
      // 62 s with NO correction: the cursor is at 1:02, not at 1:00.
      expect(screen.getByTestId('spotify-cursor')).toHaveTextContent(/^Cursor at 1:0[2-3]/)
    })

    it('is not confirmed by a nudge: the steps wait until it is confirmed or cleared', async () => {
      const api = fakeApi(routes({ [`GET ${A}/alignment`]: again(), [PUT]: alignment() }))
      await open()
      for (const id of ['nudge-plus-100', 'nudge-minus-1000', 'nudge-plus-10000']) expect(screen.getByTestId(id)).toBeDisabled()
      fireEvent.click(screen.getByTestId('nudge-plus-100'))
      expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(0)
      expect(screen.getByTestId('correction-confirm')).toBeEnabled()
      expect(screen.getByTestId('correction-clear')).toBeEnabled()
    })

    it('is used only after it is confirmed again, with the same value', async () => {
      const api = fakeApi(routes({ [`GET ${A}/alignment`]: again(), [PUT]: alignment([song(0, { correction: correction(2000) }), song(1), song(2)]) }))
      await open()
      expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(0)
      fireEvent.click(screen.getByTestId('correction-confirm'))
      await waitFor(() => { expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(1) })
      expect(api.to('PUT', `${A}/corrections/1`)[0]?.body).toEqual({ revision_id: 'r1', offset_ms: 2000 })
      await waitFor(() => { expect(screen.queryByTestId('correction-confirm')).toBeNull() })
    })
  })

  it('a correction never edits the class: no edit, no revision, no program is sent', async () => {
    const api = fakeApi(routes({ [PUT]: alignment() }))
    await open()
    fireEvent.click(screen.getByTestId('nudge-plus-100'))
    await waitFor(() => { expect(api.to('PUT', `${A}/corrections/1`)).toHaveLength(1) })
    const writes = api.calls.filter((c) => c.method !== 'GET' && !c.path.startsWith('/api/auth'))
    expect(writes.map((c) => `${c.method} ${c.path}`)).toEqual([`PUT ${A}/corrections/1`])
  })
})
