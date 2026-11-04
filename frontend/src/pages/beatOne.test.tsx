import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { beatsInWords } from '../components/classes/BeatOnePanel'
import { CLASS_PATH, MOWL_CONNECTION, REV1, classRoutes } from '../test/classFixtures'
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
    expect(await screen.findByText(/changed in another tab or window/)).toBeInTheDocument()
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
    expect(block).toHaveTextContent(/analysed again or changed/)
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

describe('beatsInWords', () => {
  it('says the shift in plain words', () => {
    expect(beatsInWords(0)).toBe('as analysed')
    expect(beatsInWords(1)).toBe('1 beat later')
    expect(beatsInWords(-3)).toBe('3 beats earlier')
  })
})
