import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { contentHash } from '../lib/canonical'
import {
  CLASS_PATH,
  CONTENT,
  MOWL_CONNECTION,
  REV1,
  classBody,
  classRoutes,
} from '../test/classFixtures'
import { fakeApi, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody, sse } from '../test/jobFixtures'
import { renderAt } from '../test/render'

const PAGE = `/classes/${CLASS_ID}`
const SAVED_ID = 'd1111111-1111-4111-8111-111111111111'
const SAVE_JOB = 'e1111111-1111-4111-8111-111111111111'
const OP = 'f1111111-1111-4111-8111-111111111111'
const OPS = '/api/external-operations'

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

function saved(extra: Record<string, unknown> = {}) {
  return {
    count: 1,
    latest_revision_number: 1,
    latest_saved_at: '2026-10-29T11:00:00Z',
    latest_up_to_date: false,
    latest_saved_workout_id: SAVED_ID,
    detached_count: 0,
    ...extra,
  }
}

function op(kind: string, extra: Record<string, unknown> = {}) {
  return {
    id: OP,
    kind,
    state: 'uncertain',
    job_id: SAVE_JOB,
    resolved_by: null,
    created_at: '2026-10-29T11:00:00Z',
    updated_at: '2026-10-29T11:00:00Z',
    resolved_at: null,
    class_id: CLASS_ID,
    pending_at: '2026-10-29T11:00:00Z',
    call_ended_at: '2026-10-29T11:00:05Z',
    actionable_at: null,
    ...extra,
  }
}

const UNSAVED = {
  count: 0,
  latest_revision_number: null,
  latest_saved_at: null,
  latest_up_to_date: null,
}

function start(cls: Record<string, unknown>, more: Record<string, object | (() => unknown)> = {}) {
  return fakeApi({
    ...SESSION_ROUTES,
    ...classRoutes({ cls }),
    'GET /api/connections': MOWL,
    ...more,
  })
}

const jobsPosts = (api: ReturnType<typeof fakeApi>) => api.to('POST', '/api/jobs')

describe('the saved state', () => {
  it('never saved: says so and offers Save only', async () => {
    start({ saved: UNSAVED })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toHaveTextContent('Not saved to MOWL yet.')
    expect(screen.getByRole('button', { name: 'Save to MOWL' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /Update/ })).toBeNull()
  })

  it('saved and up to date: says which version and offers no Update', async () => {
    start({ saved: saved({ latest_up_to_date: true }) })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toHaveTextContent(
      'Saved to MOWL as version 1. It is up to date.',
    )
    expect(screen.queryByRole('button', { name: 'Update saved workout' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Save as a new workout' })).toBeInTheDocument()
  })

  it('current differs: says so and offers Update and a new Save', async () => {
    start({ saved: saved(), current_revision_number: 2 })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toHaveTextContent(
      /Saved to MOWL as version 1\. The current version \(2\) has changes that are not saved yet/,
    )
    expect(screen.getByRole('button', { name: 'Update saved workout' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Save as a new workout' })).toBeEnabled()
  })

  it('detached after a MOWL change: explains it and offers Save only', async () => {
    start({ saved: { ...UNSAVED, detached_count: 1 } })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toHaveTextContent(
      /saved earlier to a previous MOWL connection.*cannot be updated/,
    )
    expect(screen.queryByRole('button', { name: /Update/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Save to MOWL' })).toBeEnabled()
  })

  it('offers no Update when the server names no saved workout, and says so', async () => {
    start({ saved: saved({ latest_saved_workout_id: undefined }) })
    renderAt(PAGE)
    expect(await screen.findByTestId('update-unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update saved workout' })).toBeDisabled()
  })
})

describe('Save', () => {
  it('asks first (what is written, private, your MOWL account), then sends ids and hash only', async () => {
    const api = start(
      { saved: UNSAVED },
      { 'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'save_workout' }) },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Save to MOWL' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/add version 1 of this class to your MOWL account as a new workout/)
    expect(dialog).toHaveTextContent(/private: only you can see it/)
    expect(jobsPosts(api)).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save to MOWL' }))
    await waitFor(() => {
      expect(jobsPosts(api)).toHaveLength(1)
    })
    const call = jobsPosts(api)[0]!
    expect(call.body).toEqual({
      kind: 'save_workout',
      input: {
        class_id: CLASS_ID,
        revision_id: REV1,
        content_hash: await contentHash(CONTENT),
        mowl_connection_id: MOWL_CONNECTION,
      },
    })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(call.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })

  it('says a new workout will be added when one is saved already', async () => {
    start({ saved: saved() })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Save as a new workout' }))
    expect(screen.getByRole('group', { name: 'Confirm' })).toHaveTextContent(
      /A new workout will be added; the one you saved earlier stays as it is/,
    )
  })

  it('shows progress on the running job and its result', async () => {
    let reads = 0
    const path = `/api/jobs/${SAVE_JOB}`
    start(
      {
        saved: UNSAVED,
        open_job_id: SAVE_JOB,
        open_job: { id: SAVE_JOB, kind: 'save_workout', state: 'running' },
      },
      {
        [`GET ${path}`]: () => {
          reads += 1
          return jobBody(reads === 1 ? 'running' : 'completed', { id: SAVE_JOB, kind: 'save_workout' })
        },
        [`GET ${path}/events`]: () =>
          sse([1, 'claimed', { attempt: 1 }], [2, 'stage_started', { stage: 'save' }], [3, 'completed']),
      },
    )
    renderAt(PAGE)
    await waitFor(() => {
      expect(screen.getByTestId('save-progress')).toHaveTextContent('Saving to MOWL: Writing to MOWL')
    })
    expect(screen.getByRole('button', { name: 'Save to MOWL' })).toBeDisabled()
    expect(await screen.findByText(/Saved\. The workout is in your MOWL account, private/)).toBeInTheDocument()
  })

  it('explains the write_unresolved refusal and points to the box below', async () => {
    const api = start(
      { saved: UNSAVED },
      { 'POST /api/jobs': () => reason(409, 'write_unresolved') },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Save to MOWL' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Save to MOWL' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /earlier save or update of this class to MOWL is still undecided.*Check again or Do it anyway in the box below/,
    )
    expect(screen.queryByText('write_unresolved')).toBeNull()
    await waitFor(() => {
      expect(api.to('GET', OPS).length).toBeGreaterThanOrEqual(2)
    })
  })
})

describe('Update', () => {
  it('asks first, then names the saved row, never a program id, with overwrite false', async () => {
    const api = start(
      { saved: saved() },
      { 'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }) },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update saved workout' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/first read the workout in your MOWL account/)
    expect(dialog).toHaveTextContent(/stops and writes nothing/)
    expect(jobsPosts(api)).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update workout' }))
    await waitFor(() => {
      expect(jobsPosts(api)).toHaveLength(1)
    })
    expect(jobsPosts(api)[0]!.body).toEqual({
      kind: 'update_workout',
      input: {
        class_id: CLASS_ID,
        revision_id: REV1,
        content_hash: await contentHash(CONTENT),
        mowl_connection_id: MOWL_CONNECTION,
        saved_workout_id: SAVED_ID,
        overwrite: false,
      },
    })
  })

  it('explains the write_unresolved refusal', async () => {
    start({ saved: saved() }, { 'POST /api/jobs': () => reason(409, 'write_unresolved') })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update saved workout' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Update workout' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/still undecided.*box below/)
  })

  it('after program_changed offers Update anyway, and sends overwrite only after its dialog', async () => {
    const path = `/api/jobs/${SAVE_JOB}`
    const api = start(
      {
        saved: saved(),
        open_job_id: SAVE_JOB,
        open_job: { id: SAVE_JOB, kind: 'update_workout', state: 'running' },
      },
      {
        [`GET ${path}`]: jobBody('failed', {
          id: SAVE_JOB,
          kind: 'update_workout',
          error_category: 'program_changed',
        }),
        [`GET ${path}/events`]: () => sse([1, 'failed']),
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
      },
    )
    renderAt(PAGE)
    expect(await screen.findByText(/changed in MOWL after classbeat saved it\. Nothing was written/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Update anyway (replaces your changes in MOWL)' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/replaces the workout in your MOWL account.*including any changes you made to it in MOWL/)
    expect(jobsPosts(api)).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, replace it' }))
    await waitFor(() => {
      expect(jobsPosts(api)).toHaveLength(1)
    })
    expect(jobsPosts(api)[0]!.body).toMatchObject({ input: { overwrite: true, saved_workout_id: SAVED_ID } })
  })
})

describe('an undecided write', () => {
  it('shows a save in plain words and Check again records the find', async () => {
    const api = start(
      { saved: UNSAVED },
      {
        [`GET ${OPS}`]: { operations: [op('mowl_save_workout')] },
        [`POST ${OPS}/${OP}/check-again`]: op('mowl_save_workout', { state: 'succeeded' }),
      },
    )
    renderAt(PAGE)
    const panel = await screen.findByTestId('uncertain-write')
    expect(panel).toHaveAttribute('data-kind', 'save')
    expect(panel).toHaveTextContent(/did not get a clear answer.*may be in your MOWL account, whole or only partly, or it may not be there at all/)
    fireEvent.click(within(panel).getByRole('button', { name: 'Check again' }))
    expect((await screen.findAllByText(/found the workout in your MOWL account and recorded it as saved/)).length).toBeGreaterThan(0)
    expect(api.to('POST', `${OPS}/${OP}/check-again`)).toHaveLength(1)
  })

  it('shows an update in plain words, with the stray workout', async () => {
    start(
      { saved: saved() },
      { [`GET ${OPS}`]: { operations: [op('mowl_update_workout')] } },
    )
    renderAt(PAGE)
    const panel = await screen.findByTestId('uncertain-write')
    expect(panel).toHaveAttribute('data-kind', 'update')
    expect(panel).toHaveTextContent(/unchanged, updated, or only partly updated/)
    expect(panel).toHaveTextContent(/new, empty workout with the same name in My Workouts/)
  })

  it('shows a write left by the reaper (no call end) as the same panel', async () => {
    start(
      { saved: UNSAVED },
      {
        [`GET ${OPS}`]: {
          operations: [op('mowl_save_workout', { call_ended_at: null, job_id: null })],
        },
      },
    )
    renderAt(PAGE)
    expect(await screen.findByTestId('uncertain-write')).toHaveTextContent(/could not tell whether your workout was saved/)
    expect(screen.getByRole('button', { name: 'Check again' })).toBeEnabled()
  })

  it('ignores undecided operations of other classes and of the playlist attach', async () => {
    start(
      { saved: UNSAVED },
      {
        [`GET ${OPS}`]: {
          operations: [
            op('mowl_save_workout', { class_id: 'a9999999-9999-4999-8999-999999999999' }),
            op('mowl_attach_playlist'),
          ],
        },
      },
    )
    renderAt(PAGE)
    await screen.findByTestId('saved-state')
    await waitFor(() => {
      expect(screen.queryByTestId('uncertain-write')).toBeNull()
    })
  })

  it('waits while actionable_at is in the future (call_may_be_running)', async () => {
    const api = start(
      { saved: UNSAVED },
      {
        [`GET ${OPS}`]: {
          operations: [op('mowl_save_workout', { actionable_at: '2099-01-01T00:00:00Z' })],
        },
      },
    )
    renderAt(PAGE)
    const panel = await screen.findByTestId('uncertain-write')
    expect(panel).toHaveTextContent(/may still be sending this to MOWL.*wait until/)
    expect(within(panel).getByRole('button', { name: 'Check again' })).toBeDisabled()
    expect(within(panel).getByRole('button', { name: 'Do it anyway' })).toBeDisabled()
    expect(api.to('POST', `${OPS}/${OP}/check-again`)).toHaveLength(0)
  })

  it('explains a call_may_be_running refusal from the server', async () => {
    start(
      { saved: UNSAVED },
      {
        [`GET ${OPS}`]: { operations: [op('mowl_save_workout')] },
        [`POST ${OPS}/${OP}/check-again`]: () => reason(409, 'call_may_be_running'),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/may still be writing to MOWL.*Wait a few minutes/)
  })

  it('Do it anyway for a save shows the duplicate warning first, then offers Save again', async () => {
    let decided = false
    const api = start(
      { saved: UNSAVED },
      {
        [`GET ${OPS}`]: () => ({ operations: decided ? [] : [op('mowl_save_workout')] }),
        [`POST ${OPS}/${OP}/do-it-anyway`]: () => {
          decided = true
          return op('mowl_save_workout', { state: 'failed', resolved_by: 'user' })
        },
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Do it anyway' }))
    const warning = screen.getByRole('group', { name: 'Confirm' })
    expect(warning).toHaveTextContent(/may add a second workout to your MOWL account/)
    expect(api.to('POST', `${OPS}/${OP}/do-it-anyway`)).toHaveLength(0)
    fireEvent.click(within(warning).getByRole('button', { name: 'Yes, it was not saved' }))
    expect(await screen.findByRole('button', { name: 'Save again' })).toBeEnabled()
    expect(api.to('POST', `${OPS}/${OP}/do-it-anyway`)).toHaveLength(1)
    expect(jobsPosts(api)).toHaveLength(0)
    await waitFor(() => {
      expect(screen.queryByTestId('uncertain-write')).toBeNull()
    })
  })

  it('Do it anyway for an update warns, then Update again sends overwrite only after its dialog', async () => {
    let decided = false
    const api = start(
      { saved: saved() },
      {
        [`GET ${OPS}`]: () => ({ operations: decided ? [] : [op('mowl_update_workout')] }),
        [`POST ${OPS}/${OP}/do-it-anyway`]: () => {
          decided = true
          return op('mowl_update_workout', { state: 'failed', resolved_by: 'user' })
        },
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Do it anyway' }))
    const warning = screen.getByRole('group', { name: 'Confirm' })
    expect(warning).toHaveTextContent(/partly updated; the next update replaces it/)
    fireEvent.click(within(warning).getByRole('button', { name: 'Yes, it was not updated' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Update again' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/Update anyway\?/)
    expect(jobsPosts(api)).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, replace it' }))
    await waitFor(() => {
      expect(jobsPosts(api)).toHaveLength(1)
    })
    expect(jobsPosts(api)[0]!.body).toMatchObject({ kind: 'update_workout', input: { overwrite: true } })
  })

  it('every request stays on the origin', async () => {
    const api = start({ saved: saved() }, { [`GET ${OPS}`]: { operations: [op('mowl_update_workout')] } })
    renderAt(PAGE)
    await screen.findByTestId('uncertain-write')
    for (const call of api.calls) expect(call.path.startsWith('/api/')).toBe(true)
    expect(classBody().id).toBe(CLASS_ID)
    expect(CLASS_PATH).toContain(CLASS_ID)
  })
})
