import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { localize } from '../router/paths'
import { CLASS_PATH, CONTENT, MOWL_CONNECTION, REV1, classBody, message, revisionBody, summaryBody } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import { HASH, LIST_ID, LIST_PATH, SPOTIFY_ID, listBody } from '../test/songFixtures'
import { renderAt } from '../test/render'

// The class page of a composer class with the flag on (ADR 0038 section 10.2, #1191).

const PAGE = `/classes/${CLASS_ID}`
const PENDING = `${CLASS_PATH}/pending-messages`
const MSG_ID = 'd1111111-1111-4111-8111-111111111111'
const DRAFT_ID = 'a9999999-9999-4999-8999-999999999999'
const PHASE2_JOB = 'b9999999-9999-4999-8999-999999999999'
const OK = { status: 'connected', last_error: null, connected_at: '2026-10-28T10:00:00Z', last_used_at: null }
const CONNECTIONS = {
  connections: [
    { id: MOWL_CONNECTION, provider: 'mowl', ...OK },
    { id: SPOTIFY_ID, provider: 'spotify', ...OK },
  ],
  composer_flow: true,
}
const FILL = { sketch_revision_id: REV1, attempt: 1, failure: null }
const SONG_SOURCE = (kind: string, playlist?: number) => ({ kind, ...(playlist !== undefined ? { playlist } : {}) })
// What the design heard: the first song in the warm up, the next two in the climb (CONTENT's segments).
const PREVIEW = {
  content: CONTENT,
  songs: [
    { start: 0, end: 240 },
    { start: 300, end: 540 },
    { start: 540, end: 780 },
  ],
}

interface Setup {
  cls?: Record<string, unknown>
  stored?: Record<string, unknown>[]
  origin?: string
  extraRoutes?: Record<string, unknown>
  list?: Record<string, unknown>
}

function stored(status: string, extra: Record<string, unknown> = {}) {
  return { message_id: MSG_ID, body: 'Make the last climb longer', status, reason: null, job_id: null, created_at: '2026-11-18T10:00:00Z', ...extra }
}

/** A composer class: the sketch is revision 1 unless `origin` says it is a draft. */
function setup(over: Setup = {}) {
  const origin = over.origin ?? 'sketch'
  const messages = [...(over.stored ?? [])]
  const api = fakeApi({
    ...SESSION_ROUTES,
    'GET /api/connections': CONNECTIONS,
    [`GET ${CLASS_PATH}`]: () => classBody({ sport: 'cycling', composer: true, ...over.cls }),
    [`GET ${CLASS_PATH}/revisions`]: {
      // After phase 2 the sketch stays in the history: that is what marks a composer class.
      revisions: origin === 'sketch' ? [summaryBody(REV1, 1, { origin })] : [summaryBody(DRAFT_ID, 2, { origin }), summaryBody(REV1, 1, { origin: 'sketch', is_current: false })],
    },
    [`GET ${CLASS_PATH}/messages`]: {
      messages: [message(1, 'user', 'Forty-five minutes, three climbs', { job_id: JOB_ID, base_revision_id: null })],
    },
    [`GET ${CLASS_PATH}/revisions/${REV1}`]: revisionBody(REV1, 1, CONTENT, { origin, provenance: { origin } }),
    [`GET ${CLASS_PATH}/interpretation`]: {
      interpret_job_id: JOB_ID,
      items: [],
      questions: [{ path: 'length_min', question: 'how_long', assumed: 45 }],
    },
    [`GET ${PENDING}`]: () => json({ messages }),
    [`POST ${PENDING}`]: (body) => {
      const sent = body as { message_id: string; body: string }
      const one = stored('pending', { message_id: sent.message_id, body: sent.body })
      messages.push(one)
      return json(one, 201)
    },
    [`POST ${CLASS_PATH}/fill-songs`]: { job_id: 'f1111111-1111-4111-8111-111111111111' },
    [`GET ${LIST_PATH}`]: () => listBody({ ...(over.list ?? {}) }),
    ...(over.extraRoutes as Record<string, never>),
  })
  return { api, messages }
}

const submits = (api: ReturnType<typeof setup>['api']) => api.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/submit'))
const fills = (api: ReturnType<typeof setup>['api']) => api.to('POST', `${CLASS_PATH}/fill-songs`)

describe('the composer workspace driver (ADR 0038 section 8)', () => {
  it('starts the fill by itself for a sketch with no fill started, once', async () => {
    const { api } = setup()
    renderAt(PAGE)
    expect(await screen.findByText('Rough: songs not in yet')).toBeInTheDocument()
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(fills(api)[0]!.body).toEqual({ base_revision_id: REV1 })
  })

  it('submits a pending message as the sketch revise before it starts the fill', async () => {
    const { api } = setup({ stored: [stored('pending')], extraRoutes: { [`POST ${PENDING}/${MSG_ID}/submit`]: stored('submitted', { job_id: JOB_ID }) } })
    renderAt(PAGE)
    await waitFor(() => {
      expect(submits(api)).toHaveLength(1)
    })
    expect(fills(api)).toHaveLength(0)
    expect(submits(api)[0]!.path).toBe(`${PENDING}/${MSG_ID}/submit`)
  })

  it('reads the class again when the fill job reaches the approval wait, so the list shows', async () => {
    let waiting = false
    const running = { id: JOB_ID, kind: 'design_with_songs', state: 'running' }
    const { api } = setup({
      extraRoutes: {
        [`GET ${CLASS_PATH}`]: () =>
          classBody({
            sport: 'cycling',
            composer: true,
            fill_songs: FILL,
            ...(waiting
              ? { songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }, open_job_id: JOB_ID, open_job: { ...running, state: 'waiting_for_approval' } }
              : { open_job_id: JOB_ID, open_job: running }),
          }),
        [`GET /api/jobs/${JOB_ID}`]: () => {
          waiting = true
          return json(jobBody('waiting_for_approval', { kind: 'design_with_songs' }))
        },
        [`GET /api/jobs/${JOB_ID}/events`]: () => openStream(),
      },
    })
    renderAt(PAGE)
    expect(await screen.findByText('Ready to approve')).toBeInTheDocument()
    expect(screen.queryByText('Finding songs for this shape…')).toBeNull()
    expect(api.to('GET', CLASS_PATH).length).toBeGreaterThan(1)
  })

  it('does nothing while a job runs, and does not start a fill again for a sketch whose fill was started', async () => {
    const { api } = setup({
      cls: { open_job_id: JOB_ID, open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'running' }, fill_songs: FILL },
      stored: [stored('pending')],
      extraRoutes: { [`GET /api/jobs/${JOB_ID}`]: jobBody('running', { kind: 'design_with_songs' }), [`GET /api/jobs/${JOB_ID}/events`]: () => openStream() },
    })
    renderAt(PAGE)
    expect(await screen.findByText('Finding songs for this shape…')).toBeInTheDocument()
    expect(submits(api)).toHaveLength(0)
    expect(fills(api)).toHaveLength(0)
  })

  it('saves exactly the revision and hash save_pending names, with the phase-2 job id, and offers no Save button', async () => {
    const { api } = setup({
      origin: 'design_with_songs',
      cls: { current_revision_id: DRAFT_ID, save_pending: { revision_id: DRAFT_ID, content_hash: HASH, job_id: PHASE2_JOB } },
      extraRoutes: {
        [`GET ${CLASS_PATH}/revisions/${DRAFT_ID}`]: revisionBody(DRAFT_ID, 2, CONTENT, { origin: 'design_with_songs' }),
        'POST /api/jobs': () => json(jobBody('queued', { kind: 'save_workout', id: JOB_ID }), 201),
      },
    })
    renderAt(PAGE)
    expect(await screen.findByText('Saving…')).toBeInTheDocument()
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const call = api.to('POST', '/api/jobs')[0]!
    expect(call.body).toEqual({
      kind: 'save_workout',
      input: {
        class_id: CLASS_ID,
        revision_id: DRAFT_ID,
        content_hash: HASH,
        mowl_connection_id: MOWL_CONNECTION,
        save_pending_job_id: PHASE2_JOB,
      },
    })
    expect(call.headers.get('Idempotency-Key')).toBe(`save-pending-${PHASE2_JOB}`)
    expect(screen.queryByRole('button', { name: 'Save to MOWL' })).toBeNull()
  })

  it('saves the revision save_pending names even when it is not the current revision', async () => {
    const stored_revision = 'c9999999-9999-4999-8999-999999999999'
    const stored_hash = '0f'.repeat(32)
    const { api } = setup({
      origin: 'design_with_songs',
      cls: { current_revision_id: DRAFT_ID, save_pending: { revision_id: stored_revision, content_hash: stored_hash, job_id: PHASE2_JOB } },
      extraRoutes: {
        [`GET ${CLASS_PATH}/revisions/${DRAFT_ID}`]: revisionBody(DRAFT_ID, 2, CONTENT, { origin: 'design_with_songs' }),
        'POST /api/jobs': () => json(jobBody('queued', { kind: 'save_workout', id: JOB_ID }), 201),
      },
    })
    renderAt(PAGE)
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect(api.to('POST', '/api/jobs')[0]!.body).toMatchObject({
      input: { revision_id: stored_revision, content_hash: stored_hash, save_pending_job_id: PHASE2_JOB },
    })
  })

  it('resumes after a reload: the same state gives the same save and the same key', async () => {
    const routes = {
      origin: 'design_with_songs',
      cls: { current_revision_id: DRAFT_ID, save_pending: { revision_id: DRAFT_ID, content_hash: HASH, job_id: PHASE2_JOB } },
      extraRoutes: {
        [`GET ${CLASS_PATH}/revisions/${DRAFT_ID}`]: revisionBody(DRAFT_ID, 2, CONTENT, { origin: 'design_with_songs' }),
        'POST /api/jobs': () => json(jobBody('queued', { kind: 'save_workout', id: JOB_ID }), 201),
      },
    }
    const first = setup(routes)
    const view = renderAt(PAGE)
    await waitFor(() => {
      expect(first.api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    view.unmount()
    const second = setup(routes)
    renderAt(PAGE)
    await waitFor(() => {
      expect(second.api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect(second.api.to('POST', '/api/jobs')[0]!.headers.get('Idempotency-Key')).toBe(
      first.api.to('POST', '/api/jobs')[0]!.headers.get('Idempotency-Key'),
    )
  })
})

describe('the composer workspace page', () => {
  it('shows a waiting list as ready to approve, with the ADR copy, and approves with save: true', async () => {
    const user = userEvent.setup()
    const { api } = setup({
      cls: {
        songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null },
        fill_songs: FILL,
        open_job_id: JOB_ID,
        open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'waiting_for_approval' },
      },
      list: {
        songs: [
          { position: 1, title: 'Dancing Queen', artist: 'ABBA', role: 'warm_up', bpm: 100, length_sec: 240, new: false, source: SONG_SOURCE('pinned') },
          { position: 2, title: 'The Look', artist: 'Roxette', role: 'climb', bpm: 120, length_sec: 240, new: false, source: SONG_SOURCE('playlist', 0) },
          { position: 3, title: 'Show Me Love', artist: 'Robyn', role: 'climb', bpm: 120, length_sec: 240, new: true, source: SONG_SOURCE('search') },
        ],
        preview: PREVIEW,
      },
      extraRoutes: { [`POST /api/jobs/${JOB_ID}/approve`]: jobBody('running', { kind: 'design_with_songs' }) },
    })
    renderAt(PAGE)
    expect(await screen.findByText('Ready to approve')).toBeInTheDocument()
    expect(
      await screen.findByText(
        'Approving saves a private workout to MOWL and creates a playlist in your Spotify in this order (we ask Spotify for private).',
      ),
    ).toBeInTheDocument()
    expect(await screen.findByText('Pinned by you')).toBeInTheDocument()
    expect(screen.getByText('From your playlist')).toBeInTheDocument()
    expect(screen.getByText('Found by classbeat')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Swap Dancing Queen' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Approve and save' }))
    await waitFor(() => {
      expect(api.to('POST', `/api/jobs/${JOB_ID}/approve`)).toHaveLength(1)
    })
    expect(api.to('POST', `/api/jobs/${JOB_ID}/approve`)[0]!.body).toEqual({ revision_id: LIST_ID, content_hash: HASH, save: true })
    // The fill is already started and a list waits: the driver starts no second one.
    expect(fills(api)).toHaveLength(0)
  })

  it('disables Approve while a message is pending, and shows the pending message with its state', async () => {
    setup({
      cls: {
        songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null },
        fill_songs: FILL,
        open_job_id: JOB_ID,
        open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'running' },
      },
      list: { preview: PREVIEW },
      stored: [stored('pending')],
      extraRoutes: { [`GET /api/jobs/${JOB_ID}`]: jobBody('running'), [`GET /api/jobs/${JOB_ID}/events`]: () => openStream() },
    })
    renderAt(PAGE)
    const pending = await screen.findByTestId('pending-message')
    expect(within(pending).getByText('Waiting')).toBeInTheDocument()
    expect(screen.getByText('Make the last climb longer')).toBeInTheDocument()
    // A job runs, so the chip is not "ready" yet and no Approve shows; the box is blocked by the waiting message.
    expect(screen.queryByRole('button', { name: 'Approve and save' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('holds Approve while a message waits for a list that is waiting', async () => {
    setup({
      cls: { songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }, fill_songs: FILL },
      list: { preview: PREVIEW },
      stored: [stored('pending')],
      // The submit is refused as busy, so the message stays pending.
      extraRoutes: { [`POST ${PENDING}/${MSG_ID}/submit`]: () => reason(409, 'class_busy') },
    })
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Approve and save' })).toBeDisabled()
    expect(screen.getByText('Approve waits until your message has been sent.')).toBeInTheDocument()
  })

  it('keeps Approve disabled until the stored messages have been read', async () => {
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    setup({
      cls: { songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }, fill_songs: FILL },
      list: { preview: PREVIEW },
      extraRoutes: { [`GET ${PENDING}`]: () => gate.then(() => json({ messages: [] })) },
    })
    renderAt(PAGE)
    const approve = await screen.findByRole('button', { name: 'Approve and save' })
    expect(approve).toBeDisabled()
    expect(screen.getByText('Checking for messages not sent yet…')).toBeInTheDocument()
    release()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Approve and save' })).toBeEnabled()
    })
  })

  it('offers another read when the stored messages could not be read, and enables Approve after it', async () => {
    const user = userEvent.setup()
    let reads = 0
    setup({
      cls: { songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }, fill_songs: FILL },
      list: { preview: PREVIEW },
      extraRoutes: { [`GET ${PENDING}`]: () => (++reads === 1 ? reason(500, 'internal') : json({ messages: [] })) },
    })
    renderAt(PAGE)
    expect(await screen.findByText('We could not check for messages not sent yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve and save' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Approve and save' })).toBeEnabled()
    })
  })

  it('says the playlist and the draft are being created while phase 2 runs after Approve', async () => {
    setup({
      cls: {
        fill_songs: FILL,
        songs: { state: 'approved', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null },
        open_job_id: JOB_ID,
        open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'running' },
      },
      extraRoutes: { [`GET /api/jobs/${JOB_ID}`]: jobBody('running', { kind: 'design_with_songs' }), [`GET /api/jobs/${JOB_ID}/events`]: () => openStream() },
    })
    renderAt(PAGE)
    expect(await screen.findByText('Creating your playlist and saving…')).toBeInTheDocument()
    expect(screen.queryByText('Rough: songs not in yet')).toBeNull()
  })

  it('shows a refused message as not sent, with the reason, and puts its text back in the box', async () => {
    setup({ cls: { fill_songs: FILL }, stored: [stored('failed', { reason: 'revision_limit' })] })
    renderAt(PAGE)
    const failed = await screen.findByTestId('failed-message')
    expect(within(failed).getByText('Not sent')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByLabelText('Tell classbeat what to change')).toHaveValue('Make the last climb longer')
    })
  })

  it('does not overwrite what the user is typing with a refused message', async () => {
    const user = userEvent.setup()
    setup({ cls: { fill_songs: FILL } })
    renderAt(PAGE)
    const box = await screen.findByLabelText('Tell classbeat what to change')
    await user.type(box, 'something else')
    expect(box).toHaveValue('something else')
  })

  it('sends a typed message by storing it as pending', async () => {
    const user = userEvent.setup()
    const { api } = setup({ cls: { fill_songs: FILL } })
    renderAt(PAGE)
    await user.type(await screen.findByLabelText('Tell classbeat what to change'), 'Make it harder')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => {
      expect(api.to('POST', PENDING)).toHaveLength(1)
    })
    expect(api.to('POST', PENDING)[0]!.body).toMatchObject({ body: 'Make it harder' })
  })

  it('sends a quick answer as a prepared sentence, and Keep sends nothing', async () => {
    const user = userEvent.setup()
    const { api } = setup({ cls: { fill_songs: FILL } })
    renderAt(PAGE)
    expect(await screen.findByText(/How long should the class be\? For now: 45 minutes\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '60 minutes' }))
    await waitFor(() => {
      expect(api.to('POST', PENDING)).toHaveLength(1)
    })
    expect(api.to('POST', PENDING)[0]!.body).toMatchObject({ body: 'Make the class 60 minutes long.' })
  })

  it('keeps a question open when its answer could not be stored', async () => {
    const user = userEvent.setup()
    const { api } = setup({ cls: { fill_songs: FILL }, extraRoutes: { [`POST ${PENDING}`]: () => reason(409, 'pending_full') } })
    renderAt(PAGE)
    await user.click(await screen.findByRole('button', { name: '60 minutes' }))
    await waitFor(() => {
      expect(api.to('POST', PENDING)).toHaveLength(1)
    })
    expect(await screen.findByText('This class holds as many messages as it can. Design a new class to keep going.')).toBeInTheDocument()
    expect(screen.getByText(/How long should the class be\? For now: 45 minutes\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '60 minutes' })).toBeEnabled()
  })

  it('closes a question once its answer is stored', async () => {
    const user = userEvent.setup()
    setup({ cls: { fill_songs: FILL } })
    renderAt(PAGE)
    await user.click(await screen.findByRole('button', { name: '60 minutes' }))
    await waitFor(() => {
      expect(screen.queryByText(/How long should the class be\? For now: 45 minutes\./)).toBeNull()
    })
  })

  it('disables the quick answers while the message box is blocked', async () => {
    setup({
      cls: { fill_songs: FILL, open_job_id: JOB_ID, open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'running' } },
      stored: [stored('pending')],
      extraRoutes: { [`GET /api/jobs/${JOB_ID}`]: jobBody('running', { kind: 'design_with_songs' }), [`GET /api/jobs/${JOB_ID}/events`]: () => openStream() },
    })
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: '60 minutes' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '30 minutes' })).toBeDisabled()
  })

  it('says Songs expired with Find songs again, which asks for the next attempt', async () => {
    const user = userEvent.setup()
    const { api } = setup({ cls: { fill_songs: FILL, songs: { state: 'expired', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null } } })
    renderAt(PAGE)
    expect(await screen.findByText('Songs expired')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Find songs again' }))
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(fills(api)[0]!.body).toEqual({ base_revision_id: REV1, retry: true })
  })

  it('offers Create the playlist again after a failed phase 2, and not after the sketch was revised', async () => {
    const approved = { state: 'approved', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }
    setup({ cls: { fill_songs: FILL, songs: approved, phase2: { state: 'failed' } } })
    const view = renderAt(PAGE)
    expect(await screen.findByText('The playlist was not created')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create the playlist again' })).toBeInTheDocument()
    view.unmount()
    setup({ cls: { fill_songs: { ...FILL, sketch_revision_id: 'a0000000-0000-4000-8000-000000000000' }, songs: approved, phase2: { state: 'failed' } } })
    renderAt(PAGE)
    expect(await screen.findByText('Rough: songs not in yet')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create the playlist again' })).toBeNull()
  })

  it('shows Saved for a saved draft and Update workout once it changed', async () => {
    setup({
      origin: 'design_with_songs',
      extraRoutes: { [`GET ${CLASS_PATH}/revisions/${DRAFT_ID}`]: revisionBody(DRAFT_ID, 2, CONTENT, { origin: 'design_with_songs' }) },
      cls: { current_revision_id: DRAFT_ID, current_revision_number: 2, saved: { count: 1, latest_revision_number: 1, latest_saved_at: '2026-11-18T10:00:00Z', latest_up_to_date: true, latest_saved_workout_id: 'w1' } },
    })
    renderAt(PAGE)
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })

  it('offers Update workout when the saved class has changed, and no Save button', async () => {
    setup({
      origin: 'revise_draft',
      extraRoutes: { [`GET ${CLASS_PATH}/revisions/${DRAFT_ID}`]: revisionBody(DRAFT_ID, 3, CONTENT, { origin: 'revise_draft' }) },
      cls: {
        current_revision_id: DRAFT_ID,
        current_revision_number: 3,
        saved: { count: 1, latest_revision_number: 2, latest_saved_at: '2026-11-18T10:00:00Z', latest_up_to_date: false, latest_saved_workout_id: 'w1' },
      },
    })
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Update workout' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save to MOWL' })).toBeNull()
  })

  it('swaps one song through swap-songs with an Idempotency-Key, without cancelling the waiting job first', async () => {
    const user = userEvent.setup()
    const { api } = setup({
      cls: {
        songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null },
        fill_songs: FILL,
        open_job_id: JOB_ID,
        open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'waiting_for_approval' },
      },
      list: { preview: PREVIEW },
      extraRoutes: { [`POST ${CLASS_PATH}/swap-songs`]: () => json({ job_id: 'e1111111-1111-4111-8111-111111111111', kind: 'design_with_songs', state: 'queued' }, 201) },
    })
    renderAt(PAGE)
    await user.click(await screen.findByRole('button', { name: /^Swap Hills/ }))
    await waitFor(() => {
      expect(api.to('POST', `${CLASS_PATH}/swap-songs`)).toHaveLength(1)
    })
    const call = api.to('POST', `${CLASS_PATH}/swap-songs`)[0]!
    expect(call.body).toEqual({ song_list_revision_id: LIST_ID, replace: [1] })
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
    expect(api.calls.some((c) => c.method === 'POST' && c.path.endsWith('/cancel'))).toBe(false)
  })

  it('keeps the waiting list approvable and shows the refusal when a swap is refused', async () => {
    const user = userEvent.setup()
    const { api } = setup({
      cls: {
        songs: { state: 'waiting', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null },
        fill_songs: FILL,
        open_job_id: JOB_ID,
        open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'waiting_for_approval' },
      },
      list: { preview: PREVIEW },
      extraRoutes: { [`POST ${CLASS_PATH}/swap-songs`]: () => reason(409, 'revision_limit') },
    })
    renderAt(PAGE)
    await user.click(await screen.findByRole('button', { name: /^Swap Hills/ }))
    await waitFor(() => {
      expect(api.to('POST', `${CLASS_PATH}/swap-songs`)).toHaveLength(1)
    })
    expect(await screen.findByRole('button', { name: 'Approve and save' })).toBeEnabled()
  })

  it('creates the playlist again through resume-songs: an Idempotency-Key, no body, no jobs call', async () => {
    const user = userEvent.setup()
    const { api } = setup({
      cls: { fill_songs: FILL, songs: { state: 'approved', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }, phase2: { state: 'failed' } },
      extraRoutes: { [`POST ${CLASS_PATH}/resume-songs`]: () => json({ job_id: 'e2222222-2222-4222-8222-222222222222', kind: 'design_with_songs', state: 'queued' }, 201) },
    })
    renderAt(PAGE)
    await user.click(await screen.findByRole('button', { name: 'Create the playlist again' }))
    await waitFor(() => {
      expect(api.to('POST', `${CLASS_PATH}/resume-songs`)).toHaveLength(1)
    })
    const call = api.to('POST', `${CLASS_PATH}/resume-songs`)[0]!
    expect(call.body).toEqual({})
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('asks to check the class when the final songs differed, until that draft is saved and up to date', async () => {
    const draft = {
      origin: 'design_with_songs',
      extraRoutes: { [`GET ${CLASS_PATH}/revisions/${DRAFT_ID}`]: revisionBody(DRAFT_ID, 2, CONTENT, { origin: 'design_with_songs' }) },
    }
    const unsaved = { current_revision_id: DRAFT_ID, current_revision_number: 2, phase2: { state: 'differed' } }
    setup({ ...draft, cls: unsaved })
    const view = renderAt(PAGE)
    expect(await screen.findByText('The final songs differed. Check the class and save.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save to MOWL' })).toBeInTheDocument()
    view.unmount()
    setup({
      ...draft,
      cls: { ...unsaved, saved: { count: 1, latest_revision_number: 2, latest_saved_at: '2026-11-18T10:00:00Z', latest_up_to_date: true, latest_saved_workout_id: 'w1' } },
    })
    renderAt(PAGE)
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(screen.queryByText('The final songs differed. Check the class and save.')).toBeNull()
  })

  it('keeps the More menu closed until it is opened, and lists its sections', async () => {
    const user = userEvent.setup()
    setup({ cls: { fill_songs: FILL } })
    renderAt(PAGE)
    const more = await screen.findByRole('button', { name: 'More' })
    expect(screen.queryByTestId('more-panel')).toBeNull()
    expect(screen.queryByRole('menuitem', { name: 'Versions' })).toBeNull()
    await user.click(more)
    expect(await screen.findByRole('menuitem', { name: 'Versions' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Delete class' })).toBeInTheDocument()
  })
})

describe('the driver under StrictMode', () => {
  // StrictMode runs every effect twice on mount; each step still reaches the server once.
  const strict = () => {
    window.history.replaceState(null, '', localize(PAGE, 'en'))
    return render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
  }

  it('starts the fill once', async () => {
    const { api } = setup()
    strict()
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(await screen.findByText('Rough: songs not in yet')).toBeInTheDocument()
    expect(fills(api)).toHaveLength(1)
  })

  it('submits a pending message once', async () => {
    const { api } = setup({ stored: [stored('pending')], extraRoutes: { [`POST ${PENDING}/${MSG_ID}/submit`]: stored('submitted', { job_id: JOB_ID }) } })
    strict()
    await waitFor(() => {
      expect(submits(api)).toHaveLength(1)
    })
    expect(await screen.findByText('Rough: songs not in yet')).toBeInTheDocument()
    expect(submits(api)).toHaveLength(1)
    expect(fills(api)).toHaveLength(0)
  })

  it('saves what save_pending names once', async () => {
    const { api } = setup({
      origin: 'design_with_songs',
      cls: { current_revision_id: DRAFT_ID, save_pending: { revision_id: DRAFT_ID, content_hash: HASH, job_id: PHASE2_JOB } },
      extraRoutes: {
        [`GET ${CLASS_PATH}/revisions/${DRAFT_ID}`]: revisionBody(DRAFT_ID, 2, CONTENT, { origin: 'design_with_songs' }),
        'POST /api/jobs': () => json(jobBody('queued', { kind: 'save_workout', id: JOB_ID }), 201),
      },
    })
    strict()
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect(await screen.findByText('Saving…')).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(1)
  })
})

describe('which class page renders, and what it waits for', () => {
  it('shows an older class without waiting for the connections call', async () => {
    const { api } = setup({
      cls: { composer: false },
      // The call never answers: the page must not depend on it to render.
      extraRoutes: { 'GET /api/connections': () => new Promise(() => undefined) },
    })
    renderAt(PAGE)
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Tuesday climb/ })).toBeInTheDocument()
    })
    expect(screen.queryByTestId('composer-workspace')).toBeNull()
    expect(fills(api)).toHaveLength(0)
  })

  it('shows the old page for a composer class when the flag is off', async () => {
    const { api } = setup({ extraRoutes: { 'GET /api/connections': () => json({ connections: [], composer_flow: false }) } })
    renderAt(PAGE)
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Tuesday climb/ })).toBeInTheDocument()
    })
    expect(screen.queryByTestId('composer-workspace')).toBeNull()
    expect(fills(api)).toHaveLength(0)
  })

  it('shows the old page for a composer class when the flag cannot be read', async () => {
    const { api } = setup({ extraRoutes: { 'GET /api/connections': () => json({ detail: { reason: 'internal' } }, 500) } })
    renderAt(PAGE)
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Tuesday climb/ })).toBeInTheDocument()
    })
    expect(screen.queryByTestId('composer-workspace')).toBeNull()
    expect(fills(api)).toHaveLength(0)
  })
})
