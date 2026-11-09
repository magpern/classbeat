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
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody, openStream, sse } from '../test/jobFixtures'
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

  it('puts Save to MOWL and what MOWL holds in the class header (ADR 0028 section 5)', async () => {
    start({ saved: UNSAVED })
    renderAt(PAGE)
    const header = await screen.findByTestId('class-header')
    expect(within(header).getByRole('heading', { level: 1 })).toBeInTheDocument()
    expect(within(header).getByRole('button', { name: 'Save to MOWL' })).toBeEnabled()
    expect(within(header).getByTestId('saved-state')).toHaveTextContent('Not saved to MOWL yet.')
  })

  it('saved and up to date: says which version and offers no Update', async () => {
    start({ saved: saved({ latest_up_to_date: true }) })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toHaveTextContent(
      'Saved to MOWL as version 1. Up to date.',
    )
    expect(screen.queryByRole('button', { name: 'Update workout' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Save as new' })).toBeInTheDocument()
  })

  it('current differs: says so and offers Update and a new Save', async () => {
    start({ saved: saved(), current_revision_number: 2 })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toHaveTextContent(
      /Saved to MOWL as version 1\. Version 2 has unsaved changes/,
    )
    expect(screen.getByRole('button', { name: 'Update workout' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Save as new' })).toBeEnabled()
  })

  it('detached after a MOWL change: explains it and offers Save only', async () => {
    start({ saved: { ...UNSAVED, detached_count: 1 } })
    renderAt(PAGE)
    expect(await screen.findByTestId('saved-state')).toHaveTextContent(
      /Saved earlier to an old MOWL connection.*cannot be updated/,
    )
    expect(screen.queryByRole('button', { name: /Update/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Save to MOWL' })).toBeEnabled()
  })

  it('offers no Update when the server names no saved workout, and says so', async () => {
    start({ saved: saved({ latest_saved_workout_id: undefined }) })
    renderAt(PAGE)
    expect(await screen.findByTestId('update-unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update workout' })).toBeDisabled()
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
    expect(dialog).toHaveTextContent(/Version 1 is added to your MOWL account as a new workout/)
    expect(dialog).toHaveTextContent(/Only you can see it/)
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
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new' }))
    expect(screen.getByRole('group', { name: 'Confirm' })).toHaveTextContent(
      /Your earlier workout stays as it is/,
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
    expect(await screen.findByText(/Saved to MOWL as a private workout/)).toBeInTheDocument()
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
      /earlier save or update is still undecided.*box below/,
    )
    expect(screen.queryByText('write_unresolved')).toBeNull()
    await waitFor(() => {
      expect(api.to('GET', OPS).length).toBeGreaterThanOrEqual(2)
    })
  })

  const QUOTA_REFUSALS: [string, Record<string, unknown>, RegExp][] = [
    [
      'classes',
      { quota: 'classes', limit: 3, used: 3, period_end: '2026-11-01T00:00:00Z', plan: 'free' },
      /You have used your 3 classes for October\. More from 1 November\./,
    ],
    [
      'cost',
      { quota: 'cost', period_end: '2026-11-01T00:00:00Z', plan: 'free' },
      /You have used what your plan allows for October, so no more classes can be designed for now\./,
    ],
    [
      'length',
      { quota: 'length', limit: 60, plan: 'free' },
      /Your plan allows classes of up to 60 minutes, and this version is longer, so it cannot be saved\. Change it to fit, or make an earlier version current, then save again\./,
    ],
    [
      'feature',
      { quota: 'feature', feature: 'races', plan: 'free' },
      /This version uses races, which your plan does not include, so it cannot be saved\. Change it to fit/,
    ],
    [
      'saves per hour',
      { quota: 'saves', limit: 30, used: 30, window: 'hour' },
      /You have saved or updated 30 classes in MOWL in the last hour, which is the limit\. Try again in an hour\./,
    ],
    [
      'saves per day',
      { quota: 'saves', limit: 100, used: 100, window: 'day' },
      /You have saved or updated 100 classes in MOWL in the last 24 hours, which is the limit\. Try again tomorrow\./,
    ],
  ]
  it.each(QUOTA_REFUSALS)('words the %s plan refusal with its own sentence', async (_kind, detail, sentence) => {
    start(
      { saved: UNSAVED },
      { 'POST /api/jobs': () => json({ detail: { reason: 'quota', ...detail } }, 409) },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Save to MOWL' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Save to MOWL' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(sentence)
    expect(alert).not.toHaveTextContent('what your plan allows for now')
    // The version exists already: no sentence asks for a length to be chosen.
    expect(alert).not.toHaveTextContent(/Choose a shorter length|Take that (part )?out and try again/)
  })

  it.each(QUOTA_REFUSALS)('words the %s plan refusal of an Update the same way', async (_kind, detail, sentence) => {
    start(
      { saved: saved() },
      { 'POST /api/jobs': () => json({ detail: { reason: 'quota', ...detail } }, 409) },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update workout' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Update workout' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(sentence)
    expect(alert).not.toHaveTextContent(/Choose a shorter length|Take that (part )?out and try again/)
  })
})

describe('Update', () => {
  it('asks first, then names the saved row, never a program id, with overwrite false', async () => {
    const api = start(
      { saved: saved() },
      { 'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }) },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update workout' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/We read the workout in MOWL first/)
    expect(dialog).toHaveTextContent(/nothing is written/)
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
    fireEvent.click(await screen.findByRole('button', { name: 'Update workout' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Update workout' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/still undecided.*box below/)
  })

  // Issue #504: the job can end before the reload after the click shows it open, so the summary
  // never holds an open job; the page follows the job it created.
  it('shows the program_changed sentence when the update ended before the reload saw it open', async () => {
    const path = `/api/jobs/${SAVE_JOB}`
    start(
      { saved: saved() },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
        [`GET ${path}`]: jobBody('failed', {
          id: SAVE_JOB,
          kind: 'update_workout',
          error_category: 'program_changed',
        }),
        [`GET ${path}/events`]: () => sse([1, 'failed']),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update workout' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Update workout' }))
    expect(await screen.findByText(/changed in MOWL after you saved it\. Nothing was written/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update anyway' })).toBeInTheDocument()
  })

  it('shows the Updated sentence when the update ended before the reload saw it open', async () => {
    const path = `/api/jobs/${SAVE_JOB}`
    start(
      { saved: saved({ latest_verified: true }) },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
        [`GET ${path}`]: jobBody('completed', { id: SAVE_JOB, kind: 'update_workout' }),
        [`GET ${path}/events`]: () => sse([1, 'completed']),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update workout' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Update workout' }))
    expect(await screen.findByText(/Updated\. MOWL now matches this version/)).toBeInTheDocument()
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
    expect(await screen.findByText(/changed in MOWL after you saved it\. Nothing was written/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Update anyway' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/replaces the workout in MOWL, including your changes there/)
    expect(jobsPosts(api)).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, replace it' }))
    await waitFor(() => {
      expect(jobsPosts(api)).toHaveLength(1)
    })
    expect(jobsPosts(api)[0]!.body).toMatchObject({ input: { overwrite: true, saved_workout_id: SAVED_ID } })
  })

  it('after program_other_sport says so and offers no Update anyway (issue #485)', async () => {
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
          error_category: 'program_other_sport',
        }),
        [`GET ${path}/events`]: () => sse([1, 'failed']),
      },
    )
    renderAt(PAGE)
    expect(
      await screen.findByText(/for a different sport, so it was not replaced\. Nothing was written/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Update anyway/ })).not.toBeInTheDocument()
    expect(jobsPosts(api)).toHaveLength(0)
  })
})

// Issue #560: once the outcome sentence is there the progress block (with its Stop button) goes.
describe('the progress after the outcome', () => {
  const path = `/api/jobs/${SAVE_JOB}`

  async function click(open: string, confirm: string) {
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: open }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: confirm }))
  }

  it('a Save that ended before the reload is gone from the progress when its sentence shows', async () => {
    start(
      { saved: UNSAVED },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'save_workout' }),
        [`GET ${path}`]: jobBody('completed', { id: SAVE_JOB, kind: 'save_workout' }),
        [`GET ${path}/events`]: () => sse([1, 'completed']),
      },
    )
    await click('Save to MOWL', 'Save to MOWL')
    expect(await screen.findByText(/Saved to MOWL as a private workout/)).toBeInTheDocument()
    expect(screen.queryByTestId('save-progress')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
  })

  it('an Update that ended before the reload is gone from the progress when its sentence shows', async () => {
    start(
      { saved: saved({ latest_verified: true }) },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
        [`GET ${path}`]: jobBody('completed', { id: SAVE_JOB, kind: 'update_workout' }),
        [`GET ${path}/events`]: () => sse([1, 'completed']),
      },
    )
    await click('Update workout', 'Update workout')
    expect(await screen.findByText(/Updated\. MOWL now matches this version/)).toBeInTheDocument()
    expect(screen.queryByTestId('save-progress')).toBeNull()
  })

  it('a failed Save is gone from the progress when its sentence shows', async () => {
    start(
      { saved: UNSAVED },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'save_workout' }),
        [`GET ${path}`]: jobBody('failed', { id: SAVE_JOB, kind: 'save_workout', error_category: 'upstream_unavailable' }),
        [`GET ${path}/events`]: () => sse([1, 'failed']),
      },
    )
    await click('Save to MOWL', 'Save to MOWL')
    expect(await screen.findByText(/MOWL could not be reached\. Nothing was written/)).toBeInTheDocument()
    expect(screen.queryByTestId('save-progress')).toBeNull()
  })

  it('a task that cannot be read any more says so once, drops the progress and frees the buttons', async () => {
    const api = start(
      { saved: UNSAVED },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'save_workout' }),
        [`GET ${path}`]: () => reason(404, 'not_found'),
      },
    )
    await click('Save to MOWL', 'Save to MOWL')
    const notice = await screen.findByText(/We can no longer read that task/)
    expect(notice).toHaveTextContent(/check it before you try again/)
    expect(document.body).not.toHaveTextContent('This task is no longer there')
    expect(screen.queryByTestId('save-progress')).toBeNull()
    expect(screen.getByRole('button', { name: 'Save to MOWL' })).toBeEnabled()
    // The class is read again, so a stale open job does not keep the panel waiting.
    await waitFor(() => {
      expect(api.to('GET', CLASS_PATH).length).toBeGreaterThanOrEqual(2)
    })
  })
})

describe('the "can no longer read that task" notice', () => {
  const lostPath = `/api/jobs/${SAVE_JOB}`
  const NEXT_JOB = 'e2222222-2222-4222-8222-222222222222'

  it('is gone when the next Save is started, and not before', async () => {
    let posts = 0
    start(
      { saved: UNSAVED },
      {
        'POST /api/jobs': () => {
          posts += 1
          return jobBody('queued', { id: posts === 1 ? SAVE_JOB : NEXT_JOB, kind: 'save_workout' })
        },
        [`GET ${lostPath}`]: () => reason(404, 'not_found'),
        [`GET /api/jobs/${NEXT_JOB}`]: () => jobBody('running', { id: NEXT_JOB, kind: 'save_workout' }),
        [`GET /api/jobs/${NEXT_JOB}/events`]: () => openStream(),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Save to MOWL' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Save to MOWL' }))
    expect(await screen.findByText(/We can no longer read that task/)).toBeInTheDocument()
    // A reload of the class does not clear it (it tells the user to check MOWL first) ...
    await waitFor(() => {
      expect(posts).toBe(1)
    })
    expect(screen.getByText(/We can no longer read that task/)).toBeInTheDocument()
    // ... starting the next write does.
    fireEvent.click(screen.getByRole('button', { name: 'Save to MOWL' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Save to MOWL' }))
    await waitFor(() => {
      expect(posts).toBe(2)
    })
    await waitFor(() => {
      expect(screen.queryByText(/We can no longer read that task/)).toBeNull()
    })
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
    expect(panel).toHaveTextContent(/no clear answer.*may be in your MOWL account, whole or partly, or not at all/)
    fireEvent.click(within(panel).getByRole('button', { name: 'Check again' }))
    expect((await screen.findAllByText(/found the workout in MOWL and marked it saved/)).length).toBeGreaterThan(0)
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
    expect(panel).toHaveTextContent(/unchanged or partly updated/)
    expect(panel).toHaveTextContent(/empty workout with the same name.*My Workouts/)
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
    expect(await screen.findByTestId('uncertain-write')).toHaveTextContent(/could not tell if your workout was saved/)
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
    expect(warning).toHaveTextContent(/MOWL may get a second copy/)
    expect(api.to('POST', `${OPS}/${OP}/do-it-anyway`)).toHaveLength(0)
    fireEvent.click(within(warning).getByRole('button', { name: 'Yes, it was not saved' }))
    expect(await screen.findByRole('button', { name: 'Save again' })).toBeEnabled()
    expect(api.to('POST', `${OPS}/${OP}/do-it-anyway`)).toHaveLength(1)
    expect(jobsPosts(api)).toHaveLength(0)
    await waitFor(() => {
      expect(screen.queryByTestId('uncertain-write')).toBeNull()
    })
  })

  it('Check again that proves nothing was written says so, and offers a plain Update again', async () => {
    let checked = false
    const api = start(
      { saved: saved() },
      {
        [`GET ${OPS}`]: () => ({ operations: checked ? [] : [op('mowl_update_workout')] }),
        // The backend records resolved_by = 'user' for Check again too.
        [`POST ${OPS}/${OP}/check-again`]: () => {
          checked = true
          return op('mowl_update_workout', { state: 'failed', resolved_by: 'user' })
        },
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect((await screen.findAllByText(/We checked: your MOWL workout is unchanged/)).length).toBeGreaterThan(0)
    expect(document.body).not.toHaveTextContent('Understood')
    fireEvent.click(await screen.findByRole('button', { name: 'Update again' }))
    // The plain Update dialog, not the Update anyway one: the check proved the MOWL copy untouched.
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).not.toHaveTextContent(/Update anyway\?/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update workout' }))
    await waitFor(() => {
      expect(jobsPosts(api)).toHaveLength(1)
    })
    expect(jobsPosts(api)[0]!.body).toMatchObject({ kind: 'update_workout', input: { overwrite: false } })
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


// SL6 (#532, ADR 0007 item 8): "now matches" only when the last write was read back and matched.
describe('verified or not', () => {
  const path = `/api/jobs/${SAVE_JOB}`

  function startUpdate(cls: Record<string, unknown>, more: Record<string, object | (() => unknown)> = {}) {
    return start(cls, {
      'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
      [`GET ${path}`]: jobBody('completed', { id: SAVE_JOB, kind: 'update_workout' }),
      [`GET ${path}/events`]: () => sse([1, 'completed']),
      ...more,
    })
  }

  async function runUpdate() {
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update workout' }))
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Confirm' })).getByRole('button', { name: 'Update workout' }),
    )
  }

  it('a verified Update says the MOWL workout now matches, in a polite status', async () => {
    startUpdate({ saved: saved({ latest_verified: true }) })
    await runUpdate()
    const sentence = await screen.findByText('Updated. MOWL now matches this version.')
    expect(sentence).toHaveAttribute('role', 'status')
  })

  it('an unverified Update never says it matches, and points to the MOWL app', async () => {
    startUpdate({ saved: saved({ latest_verified: false }), current_revision_number: 2 })
    await runUpdate()
    const sentence = await screen.findByText(/Updated, but we could not confirm MOWL matches/)
    expect(sentence).toHaveTextContent(/Check the workout in the MOWL app/)
    expect(sentence).toHaveAttribute('role', 'status')
    expect(screen.queryByText(/MOWL now matches this version/)).toBeNull()
  })

  it('an answer without the verified flag is not worded as a match either', async () => {
    startUpdate({ saved: saved() })
    await runUpdate()
    expect(await screen.findByText(/could not confirm MOWL matches/)).toBeInTheDocument()
    expect(screen.queryByText(/now matches this version/)).toBeNull()
  })

  it('reads the flag after the job ended, not from the page held before it', async () => {
    // The page starts on an unverified row; the answer after the job says the write was verified.
    let reads = 0
    startUpdate(
      { saved: saved({ latest_verified: false }) },
      {
        [`GET ${CLASS_PATH}`]: () => {
          reads += 1
          return classBody({ saved: saved({ latest_verified: reads > 1 }) })
        },
      },
    )
    await runUpdate()
    expect(await screen.findByText('Updated. MOWL now matches this version.')).toBeInTheDocument()
  })

  it('a failed read of the class counts as not confirmed', async () => {
    let reads = 0
    startUpdate(
      { saved: saved({ latest_verified: true }) },
      {
        [`GET ${CLASS_PATH}`]: () => {
          reads += 1
          return reads === 1 ? classBody({ saved: saved({ latest_verified: true }) }) : reason(500, 'internal')
        },
      },
    )
    await runUpdate()
    expect(await screen.findByText(/could not confirm MOWL matches/)).toBeInTheDocument()
  })
})

describe('the state line', () => {
  it('stays the plain up-to-date line whatever the verified flag (a save found by Check again has none)', async () => {
    for (const latest_verified of [true, false, undefined]) {
      start({ saved: saved({ latest_up_to_date: true, latest_verified }) })
      const view = renderAt(PAGE)
      expect(await screen.findByTestId('saved-state')).toHaveTextContent('Saved to MOWL as version 1. Up to date.')
      view.unmount()
    }
  })
})

describe('a soundless class', () => {
  it('asks to save it without music and says so when it is saved', async () => {
    const path = `/api/jobs/${SAVE_JOB}`
    const api = start(
      { soundless: true, saved: UNSAVED },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'save_workout' }),
        [`GET ${path}`]: jobBody('completed', { id: SAVE_JOB, kind: 'save_workout' }),
        [`GET ${path}/events`]: () => sse([1, 'completed']),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Save to MOWL' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/as a new workout without music\. Only you can see it/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save to MOWL' }))
    expect(await screen.findByText(/Saved to MOWL without music, as a private workout/)).toBeInTheDocument()
    // No playlist is named anywhere in what the page sends.
    expect(JSON.stringify(jobsPosts(api)[0]!.body)).not.toMatch(/playlist/)
  })

  it('a class with music keeps the plain Saved sentence and confirm text', async () => {
    start({ saved: UNSAVED })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Save to MOWL' }))
    expect(screen.getByRole('group', { name: 'Confirm' })).not.toHaveTextContent(/without music/)
  })

  it('an Update says it is written without music, and an unverified one does not claim a match', async () => {
    const path = `/api/jobs/${SAVE_JOB}`
    start(
      { soundless: true, saved: saved({ latest_verified: false }) },
      {
        'POST /api/jobs': () => jobBody('queued', { id: SAVE_JOB, kind: 'update_workout' }),
        [`GET ${path}`]: jobBody('completed', { id: SAVE_JOB, kind: 'update_workout' }),
        [`GET ${path}/events`]: () => sse([1, 'completed']),
      },
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Update workout' }))
    const dialog = screen.getByRole('group', { name: 'Confirm' })
    expect(dialog).toHaveTextContent(/version 1 replaces it, without music/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update workout' }))
    expect(await screen.findByText(/could not confirm MOWL matches/)).toBeInTheDocument()
    expect(screen.queryByText(/now matches this version/)).toBeNull()
  })
})
