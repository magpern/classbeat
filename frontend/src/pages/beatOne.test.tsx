import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { beatsInWords } from '../components/classes/BeatOnePanel'
import { CLASS_PATH, MOWL_CONNECTION, REV1, classBody, classRoutes } from '../test/classFixtures'
import { fakeApi, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// The Beat 1 panel of the class page (ADR 0007 amendment "beat-one corrections", BC5).

const PAGE = `/classes/${CLASS_ID}`
const REFIT_JOB = 'e2222222-2222-4222-8222-222222222222'

const MOWL = {
  connections: [
    {
      id: MOWL_CONNECTION,
      provider: 'mowl',
      status: 'connected',
      last_error: null,
      connected_at: '2026-10-01T10:00:00Z',
      last_used_at: null,
    },
  ],
}

function song(n: number, extra: Record<string, unknown> = {}) {
  return {
    song: n,
    start: (n - 1) * 231,
    end: n * 231,
    bpm: 128,
    estimate: false,
    title: n === 1 ? 'Opening <b>track</b>' : null,
    beats: 0,
    applied: 0,
    ...extra,
  }
}

function beatOne(extra: Record<string, unknown> = {}) {
  return {
    available: true,
    revision_id: REV1,
    songs: [song(1), song(2, { estimate: true })],
    pending: false,
    stale: [],
    ...extra,
  }
}

function start(block: unknown, more: Record<string, object | (() => unknown)> = {}) {
  return fakeApi({
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { beat_one: block } }),
    'GET /api/connections': MOWL,
    ...more,
  })
}

const beatPath = (n: number) => `${CLASS_PATH}/beat-one/${String(n)}`

describe('the Beat 1 panel', () => {
  it('is hidden without beat data, and for an answer without the block', async () => {
    start({ available: false, revision_id: REV1, songs: [], pending: false, stale: [] })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toBeInTheDocument()
    expect(screen.queryByTestId('beat-one')).toBeNull()
  })

  it('lists the songs as text with an estimate note and labelled 44 px controls', async () => {
    start(beatOne())
    renderAt(PAGE)
    const panel = await screen.findByTestId('beat-one')
    expect(within(panel).getByRole('heading', { name: 'Beat 1' })).toBeInTheDocument()
    expect(within(panel).getByText('Opening <b>track</b>')).toBeInTheDocument() // text, not HTML
    expect(within(panel).getByText('Song 2')).toBeInTheDocument()
    expect(within(panel).getByText(/From 3:51, 128 BPM/)).toBeInTheDocument()
    expect(within(panel).getByText(/Beat 1 is an estimate here/)).toBeInTheDocument()
    const later = within(panel).getByRole('button', { name: 'Move beat 1 of Song 2 later' })
    expect(later.className).toMatch(/min-h-11/)
    expect(later.className).toMatch(/min-w-11/)
    expect(within(panel).getByRole('button', { name: 'Reset beat 1 of Song 2' })).toBeDisabled()
    expect(within(panel).queryByTestId('beat-one-pending')).toBeNull()
  })

  it('sets a song a beat later, shows it in words, then offers Apply', async () => {
    const api = start(beatOne(), {
      [`PUT ${beatPath(2)}`]: beatOne({
        songs: [song(1), song(2, { estimate: true, beats: 1 })],
        pending: true,
      }),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Move beat 1 of Song 2 later' }))
    const row = await screen.findByTestId('beat-one-song-2')
    await waitFor(() => {
      expect(within(row).getByText('1 beat later')).toBeInTheDocument()
    })
    expect(api.to('PUT', beatPath(2))[0]?.body).toEqual({ beats: 1, revision_id: REV1 })
    expect(screen.getByTestId('beat-one-pending')).toHaveTextContent(
      /does not use your timing fixes yet/,
    )
    expect(within(row).queryByText(/estimate here/)).toBeNull()
  })

  it('resets with DELETE for the current version', async () => {
    const del = `DELETE ${beatPath(1)}?revision_id=${REV1}`
    const api = start(beatOne({ songs: [song(1, { beats: -2 }), song(2)], pending: true }), {
      [del]: beatOne({ songs: [song(1), song(2)] }),
    })
    renderAt(PAGE)
    const row = await screen.findByTestId('beat-one-song-1')
    expect(within(row).getByText('2 beats earlier')).toBeInTheDocument()
    fireEvent.click(within(row).getByRole('button', { name: /Reset beat 1 of Opening/ }))
    await waitFor(() => {
      expect(within(row).getByText('as analysed')).toBeInTheDocument()
    })
    expect(api.to('DELETE', `${beatPath(1)}?revision_id=${REV1}`)).toHaveLength(1)
    expect(screen.queryByTestId('beat-one-pending')).toBeNull()
  })

  it('stops at three beats either way', async () => {
    start(beatOne({ songs: [song(1, { beats: 3 }), song(2, { beats: -3 })], pending: true }))
    renderAt(PAGE)
    expect(
      await screen.findByRole('button', { name: /Move beat 1 of Opening .* later/ }),
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move beat 1 of Song 2 earlier' })).toBeDisabled()
  })

  it('Apply timing fixes creates a refit job with ids only and an idempotency key', async () => {
    const api = start(beatOne({ songs: [song(1, { beats: 1 }), song(2)], pending: true }), {
      'POST /api/jobs': () => jobBody('queued', { id: REFIT_JOB, kind: 'refit_draft' }),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Apply timing fixes' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'refit_draft',
      input: { class_id: CLASS_ID, base_revision_id: REV1, mowl_connection_id: MOWL_CONNECTION },
    })
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('a refusal is shown as a sentence and the page reloads on a stale version', async () => {
    const api = start(beatOne(), {
      [`PUT ${beatPath(1)}`]: () => reason(409, 'stale_base'),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: /Move beat 1 of Opening .* earlier/ }))
    expect(await screen.findByText(/changed in another tab/)).toBeInTheDocument()
    await waitFor(() => {
      expect(api.to('GET', CLASS_PATH).length).toBeGreaterThan(1)
    })
  })

  it('lists corrections for earlier music, clears one by id and all at once', async () => {
    const ROW = 'b3333333-3333-4333-8333-333333333333'
    const stale = [{ id: ROW, song: 4, beats: -1 }]
    const api = start(beatOne({ stale }), {
      [`DELETE ${CLASS_PATH}/beat-one/rows/${ROW}`]: beatOne(),
      [`DELETE ${CLASS_PATH}/beat-one`]: beatOne(),
    })
    renderAt(PAGE)
    const block = await screen.findByTestId('beat-one-stale')
    expect(block).toHaveTextContent(/for music this version does not play/)
    expect(block).toHaveTextContent('Song 4: 1 beat earlier')
    fireEvent.click(within(block).getByRole('button', { name: 'Clear the earlier timing fix of song 4' }))
    await waitFor(() => {
      expect(screen.queryByTestId('beat-one-stale')).toBeNull()
    })
    expect(api.to('DELETE', `${CLASS_PATH}/beat-one/rows/${ROW}`)).toHaveLength(1)
  })

  it('Clear all timing fixes sends one DELETE for the class', async () => {
    const api = start(beatOne({ stale: [{ id: 'b4444444-4444-4444-8444-444444444444', song: 1, beats: 2 }] }), {
      [`DELETE ${CLASS_PATH}/beat-one`]: beatOne(),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Clear all timing fixes' }))
    await waitFor(() => {
      expect(api.to('DELETE', `${CLASS_PATH}/beat-one`)).toHaveLength(1)
    })
  })

  it('asks to listen again when the tempo of a corrected song changed', async () => {
    start(beatOne({ songs: [song(1, { beats: 1, applied: 1, bpm_changed: true }), song(2)] }))
    renderAt(PAGE)
    expect(await screen.findByText(/tempo of this song changed/)).toBeInTheDocument()
  })

  it('says so when there is nothing to apply', async () => {
    start(beatOne({ songs: [song(1, { beats: 1 }), song(2)], pending: true }), {
      'POST /api/jobs': () => reason(409, 'nothing_pending'),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Apply timing fixes' }))
    expect(await screen.findByText('The current version already uses these timing fixes.')).toBeInTheDocument()
  })
})

// Every refusal the panel can meet, as the sentence the user reads (never the code).
const SENTENCES: [string, number, string, string][] = [
  ['correction_limit', 409, 'PUT', 'This class has as many timing fixes as it can hold. Reset some, then try again.'],
  ['no_beat_data', 409, 'PUT', 'This version has no beat data to correct. Ask for a change by chat first, then try again.'],
  ['refit_limit', 409, 'POST', 'This class has had its timing fixed as often as it can. Design a new class to start again.'],
  ['invalid_input', 422, 'DELETE', 'That request could not be used. Check it and try again.'],
]

describe('the Beat 1 panel refusals and guards', () => {
  it.each(SENTENCES)('%s (%i on %s) is shown as its sentence', async (why, status, method, sentence) => {
    const del = `DELETE ${beatPath(1)}?revision_id=${REV1}`
    start(beatOne({ songs: [song(1, { beats: 1 }), song(2)], pending: true }), {
      [`PUT ${beatPath(1)}`]: () => reason(status, why),
      [del]: () => reason(status, why),
      'POST /api/jobs': () => reason(status, why),
    })
    renderAt(PAGE)
    const name =
      method === 'PUT'
        ? /Move beat 1 of Opening .* later/
        : method === 'DELETE'
          ? /Reset beat 1 of Opening/
          : 'Apply timing fixes'
    fireEvent.click(await screen.findByRole('button', { name }))
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(why))).toBeNull()
  })

  it('Apply waits while the class has an open job', async () => {
    const api = start(beatOne({ songs: [song(1, { beats: 1 }), song(2)], pending: true }), {
      [`GET ${CLASS_PATH}`]: classBody({
        beat_one: beatOne({ songs: [song(1, { beats: 1 }), song(2)], pending: true }),
        open_job_id: REFIT_JOB,
        open_job: { id: REFIT_JOB, kind: 'revise_draft', state: 'running' },
      }),
      [`GET /api/jobs/${REFIT_JOB}`]: jobBody('running', { id: REFIT_JOB, kind: 'revise_draft' }),
      [`GET /api/jobs/${REFIT_JOB}/events`]: () => new Response('', { status: 204 }),
    })
    renderAt(PAGE)
    const pending = await screen.findByTestId('beat-one-pending')
    const button = within(pending).getByRole('button', { name: 'Apply timing fixes' })
    expect(button).toBeDisabled()
    expect(pending).toHaveTextContent('Wait for the running task to finish first.')
    fireEvent.click(button)
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('while a change is on its way, no other change or Apply can be sent', async () => {
    const answer = beatOne({ songs: [song(1, { beats: 2 }), song(2)], pending: true })
    start(beatOne({ songs: [song(1, { beats: 1 }), song(2)], pending: true }), {
      [`PUT ${beatPath(1)}`]: answer,
    })
    const inner = globalThis.fetch
    const held: (() => void)[] = []
    const changes: string[] = []
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      if (method === 'GET') return inner(input, init)
      changes.push(`${method} ${typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url}`)
      return new Promise<Response>((resolve) => {
        held.push(() => {
          resolve(inner(input, init))
        })
      })
    })
    renderAt(PAGE)
    const later = await screen.findByRole('button', { name: /Move beat 1 of Opening .* later/ })
    fireEvent.click(later)
    await waitFor(() => {
      expect(changes).toHaveLength(1)
    })
    const earlier = screen.getByRole('button', { name: /Move beat 1 of Opening .* earlier/ })
    const reset = screen.getByRole('button', { name: /Reset beat 1 of Opening/ })
    const apply = screen.getByRole('button', { name: 'Apply timing fixes' })
    for (const button of [later, earlier, reset, apply]) {
      expect(button).toBeDisabled()
      fireEvent.click(button)
    }
    expect(changes).toEqual([`PUT ${beatPath(1)}`])
    held.forEach((release) => {
      release()
    })
    const row = screen.getByTestId('beat-one-song-1')
    await waitFor(() => {
      expect(within(row).getByText('2 beats later')).toBeInTheDocument()
    })
    expect(later).toBeEnabled()
  })

  it('a retry of Apply with the same fixes reuses its idempotency key', async () => {
    let posts = 0
    const api = start(beatOne({ songs: [song(1, { beats: 1 }), song(2)], pending: true }), {
      'POST /api/jobs': () => {
        posts += 1
        return posts === 1
          ? reason(503, 'unavailable')
          : jobBody('queued', { id: REFIT_JOB, kind: 'refit_draft' })
      },
    })
    renderAt(PAGE)
    const apply = await screen.findByRole('button', { name: 'Apply timing fixes' })
    fireEvent.click(apply)
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    await waitFor(() => {
      expect(apply).toBeEnabled()
    })
    fireEvent.click(apply)
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(2)
    })
    const [first, second] = api.to('POST', '/api/jobs')
    expect(first?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(second?.headers.get('Idempotency-Key')).toBe(first?.headers.get('Idempotency-Key'))
  })

  it('announces the shift of a song politely', async () => {
    start(beatOne())
    renderAt(PAGE)
    const row = await screen.findByTestId('beat-one-song-2')
    const output = within(row).getByText('as analysed')
    expect(output.tagName).toBe('OUTPUT')
    expect(output).toHaveAttribute('aria-live', 'polite')
  })

  it('takes the server view when a refusal reloads the same version', async () => {
    let gets = 0
    const after = beatOne({ songs: [song(1, { beats: 2 }), song(2)], pending: true })
    const api = start(beatOne(), {
      [`GET ${CLASS_PATH}`]: () => {
        gets += 1
        return classBody({ beat_one: gets === 1 ? beatOne() : after })
      },
      [`PUT ${beatPath(1)}`]: () => reason(409, 'class_busy'),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: /Move beat 1 of Opening .* later/ }))
    const row = await screen.findByTestId('beat-one-song-1')
    await waitFor(() => {
      expect(within(row).getByText('2 beats later')).toBeInTheDocument()
    })
    expect(api.to('GET', CLASS_PATH).length).toBeGreaterThan(1)
    expect(screen.getByTestId('beat-one-pending')).toBeInTheDocument()
  })
})

describe('beatsInWords', () => {
  it('says the shift in plain words', () => {
    expect(beatsInWords(0)).toBe('as analysed')
    expect(beatsInWords(1)).toBe('1 beat later')
    expect(beatsInWords(-3)).toBe('3 beats earlier')
  })
})
