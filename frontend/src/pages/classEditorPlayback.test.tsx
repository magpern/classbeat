import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CLASS_PATH, REV1, REV2, classBody } from '../test/classFixtures'
import { EDITS_PATH, EDIT_PAGE, HASH2, PREVIEW_PATH, PREVIEW_SESSION, clipPath, editorRoutes, overlayBody, previewRoutes, readyClip, refusal, timelineBody } from '../test/editorApi'
import { fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'

// The audio preview on the editor page (CP-R3): Play and Pause, the 15-minute pause, a session that
// ended, an edit during playback. The server is the fake `fetch`; the browser's media events are
// fired by the test on the real (jsdom) audio elements; the listening counter's clock is a
// `performance.now` the test moves.

const SESSIONS = `POST ${PREVIEW_PATH}/sessions`
const LIMIT = 'Preview paused after 15 minutes. Press Play to continue.'
let offsetMs = 0

beforeEach(() => {
  offsetMs = 0
  const real = performance.now.bind(performance)
  vi.spyOn(performance, 'now').mockImplementation(() => real() + offsetMs)
})

afterEach(() => {
  vi.restoreAllMocks()
})

const activeAudio = () => document.querySelector('audio[src]')
const heard = (type: string) => {
  act(() => {
    activeAudio()?.dispatchEvent(new Event(type))
  })
}
const play = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Play' }))
  await waitFor(() => { expect(activeAudio()).not.toBeNull() })
  heard('playing')
  await screen.findByRole('button', { name: 'Pause' })
}
const sessions = (api: ReturnType<typeof fakeApi>) => api.to('POST', `${PREVIEW_PATH}/sessions`).length
const clips = (api: ReturnType<typeof fakeApi>, startMs: number) => api.to('GET', clipPath(startMs).replace('GET ', '')).length
const open = async () => {
  renderAt(EDIT_PAGE)
  await screen.findByTestId('editor-timeline')
}

describe('Play and Pause', () => {
  it('Play starts a session and asks for the window at the start; the cursor appears when the audio plays', async () => {
    const api = fakeApi(editorRoutes(previewRoutes()))
    await open()
    expect(sessions(api)).toBe(0) // nothing is asked for before Play
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await waitFor(() => { expect(activeAudio()).not.toBeNull() })
    expect(sessions(api)).toBe(1)
    expect(clips(api, 0)).toBe(1)
    expect(activeAudio()?.getAttribute('src')).toBe(`${PREVIEW_PATH}/clip?session=${PREVIEW_SESSION}&start_ms=0`)
    expect(screen.queryByTestId('cursor')).toBeNull() // asked for, not yet heard
    heard('playing')
    expect(await screen.findByTestId('cursor')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
  })

  it('Pause ends the session: the next Play is a new one', async () => {
    const api = fakeApi(editorRoutes(previewRoutes()))
    await open()
    await play()
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await screen.findByRole('button', { name: 'Play' })
    expect(screen.getByTestId('cursor')).toBeInTheDocument() // paused: the position is still known
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await waitFor(() => { expect(sessions(api)).toBe(2) })
  })

  it('has no play control on an older Spotify draft (no stored map), which opens read-only', async () => {
    // A Spotify class, not the studio's, whose draft has no stored map: the server answers edit_unavailable.
    const api = fakeApi(editorRoutes(previewRoutes({ [`GET ${CLASS_PATH}`]: classBody(), [`GET ${CLASS_PATH}/overlay`]: () => refusal('edit_unavailable') })))
    renderAt(EDIT_PAGE)
    expect(await screen.findByTestId('readonly-edit_unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /play|pause/i })).toBeNull()
    expect(document.querySelector('audio')).toBeNull()
    expect(sessions(api)).toBe(0)
  })

  it('falls back to the silent cursor, and says so, when the server has no preview', async () => {
    fakeApi(editorRoutes({ [SESSIONS]: () => json({ detail: { reason: 'not_found' } }, 404) }))
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(await screen.findByText(/audio preview is not available here/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Play' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Song 2: Second song/ }))
    expect(await screen.findByTestId('cursor')).toBeInTheDocument() // the silent clock
  })

  it('says a refusal in words with a Try again, never the code', async () => {
    let limited = true
    fakeApi(editorRoutes(previewRoutes({ [SESSIONS]: () => (limited ? json({ detail: { reason: 'rate_limited' } }, 429) : { session_id: PREVIEW_SESSION }) })))
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(await screen.findByText(/Too many previews in a short time/)).toBeInTheDocument()
    expect(screen.queryByText(/rate_limited/)).toBeNull()
    limited = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => { expect(activeAudio()).not.toBeNull() })
  })

  it('says that a window is rendering, in words', async () => {
    let calls = 0
    fakeApi(
      editorRoutes(
        previewRoutes({
          [clipPath(0)]: () => {
            calls += 1
            return calls === 1 ? new Response(null, { status: 202, headers: { 'Retry-After': '1' } }) : readyClip(0, 240_000)
          },
        }),
      ),
    )
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(await screen.findByText(/Rendering this part of the class/)).toBeInTheDocument()
    await waitFor(() => { expect(activeAudio()).not.toBeNull() }, { timeout: 4000 })
  })
})

describe('the 15-minute limit', () => {
  it('pauses at 15 minutes of playing time with the exact message, and Play starts a new session', async () => {
    const api = fakeApi(editorRoutes(previewRoutes()))
    await open()
    await play()
    offsetMs += 899_000
    await act(() => new Promise((r) => setTimeout(r, 1200))) // the 1 s tick: 899 s plus about 1 s
    expect(screen.queryByText(LIMIT)).toBeNull()
    offsetMs += 2000
    expect(await screen.findByText(LIMIT)).toBeInTheDocument()
    expect(activeAudio()).toBeNull() // the session ended and let go of the audio
    expect(sessions(api)).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await waitFor(() => { expect(sessions(api)).toBe(2) })
    await waitFor(() => { expect(activeAudio()).not.toBeNull() })
    heard('playing')
    expect(screen.queryByText(LIMIT)).toBeNull()
    await act(() => new Promise((r) => setTimeout(r, 1200)))
    expect(screen.queryByText(LIMIT)).toBeNull() // the counter started again at 0
  })

  it('does not count the time the page was not playing (a seek keeps the session and the counter)', async () => {
    const api = fakeApi(editorRoutes(previewRoutes({ [clipPath(674_000)]: () => readyClip(674_000, 914_000) })))
    await open()
    await play()
    offsetMs += 600_000
    fireEvent.click(screen.getByRole('button', { name: /^Song 2: Second song/ }))
    await waitFor(() => { expect(clips(api, 674_000)).toBe(1) })
    expect(sessions(api)).toBe(1)
    heard('playing')
    offsetMs += 200_000
    await act(() => new Promise((r) => setTimeout(r, 1200)))
    expect(screen.queryByText(LIMIT)).toBeNull() // 800 s
    offsetMs += 100_000
    expect(await screen.findByText(LIMIT)).toBeInTheDocument() // 900 s: the seek did not reset it
  })
})

describe('a session that ended on the server', () => {
  it('says so on a 410 and the next Play is a new session, never the old one', async () => {
    const api = fakeApi(editorRoutes(previewRoutes({ [clipPath(674_000)]: () => json({ detail: { reason: 'preview_session_ended' } }, 410) })))
    await open()
    await play()
    fireEvent.click(screen.getByRole('button', { name: /^Song 2: Second song/ }))
    expect(await screen.findByText('The preview session has ended. Press Play to continue.')).toBeInTheDocument()
    expect(sessions(api)).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await waitFor(() => { expect(sessions(api)).toBe(2) })
  })
})

describe('an edit while the audio plays', () => {
  const drag = async () => {
    const handle = await screen.findByRole('slider', { name: /^End of block 1,/ })
    handle.focus()
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
  }
  const edited = (changeAudio: boolean) => {
    let done = false
    return editorRoutes(
      previewRoutes({
        [`POST ${EDITS_PATH}`]: () => {
          done = true
          return { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] }
        },
        // The overlay and the timeline answer for the same draft: the one the edit made once it is done.
        [`GET ${CLASS_PATH}/overlay`]: () => overlayBody(done ? REV2 : REV1),
        [`GET ${CLASS_PATH}/timeline`]: () => (done && changeAudio ? { ...timelineBody(REV2), spec_digest: 'cd'.repeat(32) } : timelineBody(done ? REV2 : REV1)),
      }),
    )
  }

  it('an audio change fetches fresh clips in the same session and keeps the counter', async () => {
    const api = fakeApi(edited(true))
    await open()
    await play()
    offsetMs += 600_000
    await drag()
    await waitFor(() => { expect(clips(api, 0)).toBe(2) }) // the stale clip is asked for again
    expect(sessions(api)).toBe(1)
    heard('playing')
    expect(screen.queryByText(LIMIT)).toBeNull()
    offsetMs += 300_000
    expect(await screen.findByText(LIMIT)).toBeInTheDocument() // 600 s + 300 s: the edit did not reset it
  })

  it('a workout-only edit keeps the clips it has and the counter', async () => {
    const api = fakeApi(edited(false))
    await open()
    await play()
    offsetMs += 600_000
    await drag()
    await waitFor(() => { expect(api.to('GET', `${CLASS_PATH}/overlay`).length).toBeGreaterThan(1) })
    await waitFor(() => { expect(api.to('GET', `${CLASS_PATH}/timeline`).length).toBeGreaterThan(1) })
    expect(clips(api, 0)).toBe(1)
    expect(sessions(api)).toBe(1)
    offsetMs += 300_000
    expect(await screen.findByText(LIMIT)).toBeInTheDocument()
  })
})
