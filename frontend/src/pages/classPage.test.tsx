import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { contentHash } from '../lib/canonical'
import {
  CLASS_PATH,
  CONTENT,
  MOWL_CONNECTION,
  REV1,
  REV2,
  REVISE_JOB,
  classBody,
  classRoutes,
  message,
  revisionBody,
  summaryBody,
} from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody, sse } from '../test/jobFixtures'
import { renderAt } from '../test/render'

const PAGE = `/classes/${CLASS_ID}`
const REVISE_JOB_PATH = `/api/jobs/${REVISE_JOB}`

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

const OPEN_REVISE = {
  open_job_id: REVISE_JOB,
  open_job: { id: REVISE_JOB, kind: 'revise_draft', state: 'running' },
}

const TWO_REVISIONS = [summaryBody(REV1, 1), summaryBody(REV2, 2, { is_current: false })]

const CONTENT_V2 = {
  ...CONTENT,
  name: 'Tuesday climb, longer',
  segments: CONTENT.segments.map((s) => ({ ...s })),
}

function everyRequestStaysOnTheOrigin(api: { calls: { path: string }[] }) {
  expect(api.calls.length).toBeGreaterThan(0)
  for (const call of api.calls) expect(call.path.startsWith('/api/')).toBe(true)
}

describe('the class page', () => {
  it('says so when the class has no version yet and offers no chat', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({ cls: { current_revision_id: null, current_revision_number: null, revision_count: 0 }, revisions: [] }),
    })
    renderAt(PAGE)
    expect(await screen.findByText(/This class has no version yet/)).toBeInTheDocument()
    expect(screen.getByText('There is no version to change yet.')).toBeInTheDocument()
    expect(screen.getByLabelText('What should change?')).toBeDisabled()
    expect(screen.getByText('There is no version yet.')).toBeInTheDocument()
  })

  it('shows the version with its chart, length, TSS, segments and warnings, and never as HTML', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        messages: [
          message(1, 'user', 'Make it hillier', { result_revision_id: null }),
          message(2, 'assistant', '', {
            result_revision_id: REV1,
            change: { music_changed: true },
          }),
        ],
      }),
    })
    const { container } = renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Tuesday climb <b>bold</b>' })).toBeInTheDocument()
    expect(screen.getByTestId('duration')).toHaveTextContent('22:00')
    expect(screen.getByTestId('tss')).toHaveTextContent(/^26\.[34]$/)
    expect(screen.getByRole('img', { name: /Effort over time.*3 segments, 22:00/ })).toBeInTheDocument()
    expect(container.querySelectorAll('.timeline__bar')).toHaveLength(4)
    expect(screen.getByRole('region', { name: 'Climb' })).toBeInTheDocument()
    expect(screen.getByTestId('unknown-bpm')).toHaveTextContent('Song <i>without</i> BPM')
    expect(screen.getByTestId('music-changed')).toBeInTheDocument()
    // Model and user text is text: no element was made from it.
    for (const tag of ['img', 'script', 'b', 'i']) expect(container.querySelector(tag)).toBeNull()
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument()
    everyRequestStaysOnTheOrigin(api)
  })

  it('shows no TSS note when the backend did not flag the version', async () => {
    fakeApi({ ...SESSION_ROUTES, ...classRoutes() })
    renderAt(PAGE)
    await screen.findByTestId('tss')
    expect(screen.getByTestId('tss')).toHaveTextContent(/^26\.[34]$/)
    expect(screen.queryByTestId('tss-note')).toBeNull()
  })

  it('shows the note next to the TSS when the backend flags the version as approximate', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        revisionBodies: { [REV1]: revisionBody(REV1, 1, CONTENT, { tss_approximate: true }) },
      }),
    })
    renderAt(PAGE)
    expect(await screen.findByTestId('tss-note')).toHaveTextContent(
      'approximate: zone 7 has no exact TSS weight',
    )
    const hidden = within(screen.getByTestId('tss')).getByText(
      /^26\.[34], approximate: zone 7 has no exact TSS weight$/,
    )
    expect(hidden).toHaveClass('sr-only')
    expect(screen.getByTestId('tss')).toHaveTextContent(/^≈ 26\.[34]/)
  })

  it('lists two versions with the current one marked, and views the other', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: { current_revision_id: REV1, revision_count: 2 },
        revisions: TWO_REVISIONS,
        revisionBodies: { [REV2]: revisionBody(REV2, 2, CONTENT_V2) },
      }),
    })
    renderAt(PAGE)
    const panel = (await screen.findByRole('heading', { name: 'Versions' })).closest('section')!
    const rows = within(panel).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Version 2')
    expect(rows[0]).toHaveTextContent('Changed on request, from version 1')
    expect(rows[1]).toHaveTextContent('Version 1')
    expect(rows[1]).toHaveTextContent('Current')
    expect(screen.queryByTestId('not-current')).toBeNull()
    fireEvent.click(within(panel).getByRole('button', { name: 'View version 2' }))
    expect(await screen.findByRole('heading', { name: 'Tuesday climb, longer' })).toBeInTheDocument()
    expect(screen.getByTestId('not-current')).toHaveTextContent('You are looking at version 2')
  })

  it('uses a version that is not current, sending the hash of the content shown', async () => {
    let current = REV1
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        revisions: TWO_REVISIONS,
        revisionBodies: { [REV2]: revisionBody(REV2, 2, CONTENT_V2) },
      }),
      [`GET ${CLASS_PATH}`]: () => classBody({ current_revision_id: current, current_revision_number: current === REV1 ? 1 : 2 }),
      [`POST ${CLASS_PATH}/current`]: (body) => {
        current = (body as { revision_id: string }).revision_id
        return classBody({ current_revision_id: current, current_revision_number: 2 })
      },
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Use version 2' }))
    await waitFor(() => {
      expect(api.to('POST', `${CLASS_PATH}/current`)).toHaveLength(1)
    })
    const call = api.to('POST', `${CLASS_PATH}/current`)[0]!
    expect(call.body).toEqual({ revision_id: REV2, content_hash: await contentHash(CONTENT_V2) })
    expect(call.headers.get('X-CSRF-Token')).toBe('csrf-session')
    await waitFor(() => {
      expect(api.to('GET', CLASS_PATH).length).toBeGreaterThanOrEqual(2)
    })
  })

  it('shows the reason and reloads when the version changed elsewhere (409 hash_mismatch)', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        revisions: TWO_REVISIONS,
        revisionBodies: { [REV2]: revisionBody(REV2, 2, CONTENT_V2) },
      }),
      [`POST ${CLASS_PATH}/current`]: () => reason(409, 'hash_mismatch'),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Use version 2' }))
    expect(await screen.findByText(/That version is not the one this page showed/)).toBeInTheDocument()
    await waitFor(() => {
      expect(api.to('GET', CLASS_PATH).length).toBeGreaterThanOrEqual(2)
    })
  })

  it('shows the live progress of a running change and reloads when it ends', async () => {
    let jobReads = 0
    let classReads = 0
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({ messages: [message(1, 'user', 'Longer climb')] }),
      'GET /api/connections': MOWL,
      [`GET ${CLASS_PATH}`]: () => {
        classReads += 1
        return classBody(classReads === 1 ? OPEN_REVISE : {})
      },
      [`GET ${REVISE_JOB_PATH}`]: () => {
        jobReads += 1
        return jobBody(jobReads === 1 ? 'running' : 'completed', { id: REVISE_JOB, kind: 'revise_draft' })
      },
      [`GET ${REVISE_JOB_PATH}/events`]: () =>
        sse([1, 'claimed', { attempt: 1 }], [2, 'stage_started', { stage: 'revise' }], [3, 'completed']),
    })
    renderAt(PAGE)
    await waitFor(() => {
      expect(screen.getByTestId('revise-progress')).toHaveTextContent('Changing the class')
    })
    expect(screen.getByLabelText('What should change?')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop changing' })).toBeInTheDocument()
    await waitFor(() => {
      expect(api.to('GET', CLASS_PATH).length).toBeGreaterThanOrEqual(2)
    })
    await waitFor(() => {
      expect(screen.queryByTestId('revise-progress')).toBeNull()
    })
    expect(screen.getByLabelText('What should change?')).toBeEnabled()
  })

  it('says a design is running when the open job is the first design', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: {
          current_revision_id: null,
          current_revision_number: null,
          open_job: { id: REVISE_JOB, kind: 'design_draft', state: 'running' },
        },
        revisions: [],
      }),
    })
    renderAt(PAGE)
    expect(await screen.findByText(/This class is being designed/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See how it is going' })).toHaveAttribute('href', `/jobs/${REVISE_JOB}`)
  })

  it.each([
    ['invalid_output', /did not produce a usable change.*Nothing changed/],
    ['music_unlinked', /music of this version is not in your connected MOWL account/],
    ['upstream_unavailable', /MOWL could not be reached\. Nothing changed/],
  ])('shows a failed change (%s) in plain words and keeps the request', async (category, sentence) => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({ messages: [message(1, 'user', 'Make it <u>harder</u>')] }),
      [`GET ${REVISE_JOB_PATH}`]: jobBody('failed', { id: REVISE_JOB, error_category: category }),
    })
    const { container } = renderAt(PAGE)
    expect(await screen.findByText('Make it <u>harder</u>')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent(sentence)
    expect(screen.queryByText(category)).toBeNull()
    expect(container.querySelector('u')).toBeNull()
  })

  it('shows the change summary and the designer note as plain text', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: { revision_count: 2 },
        revisions: TWO_REVISIONS,
        messages: [
          message(1, 'user', 'Longer <b>climb</b>'),
          message(2, 'assistant', 'I made it <img src=x onerror=alert(1)> longer', {
            result_revision_id: REV2,
            change: {
              segments: { added: 0, removed: 1 },
              intervals: { added: 2, removed: 0, changed: 3 },
              duration_sec: { before: 1200, after: 1500 },
              tss: { before: 40.5, after: 45 },
              text_changed: true,
            },
          }),
        ],
      }),
    })
    const { container } = renderAt(PAGE)
    const history = await screen.findByRole('list', { name: 'Your requests' })
    expect(within(history).getByText('Longer <b>climb</b>')).toBeInTheDocument()
    expect(within(history).getByText('Segments: 0 added, 1 removed.')).toBeInTheDocument()
    expect(within(history).getByText('Intervals: 2 added, 0 removed, 3 changed.')).toBeInTheDocument()
    expect(within(history).getByText('Length: 20:00 to 25:00.')).toBeInTheDocument()
    expect(within(history).getByText('TSS: 40.5 to 45.')).toBeInTheDocument()
    expect(within(history).getByText('Names, description or cues changed.')).toBeInTheDocument()
    expect(history).toHaveTextContent('I made it <img src=x onerror=alert(1)> longer')
    expect(within(history).getByRole('button', { name: 'View version 2' })).toBeInTheDocument()
    for (const tag of ['img', 'b']) expect(container.querySelector(tag)).toBeNull()
  })
})

describe('asking for a change', () => {
  it('sends the request against the version shown, with the MOWL connection and a key', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes(),
      'GET /api/connections': MOWL,
      'POST /api/jobs': () => jobBody('queued', { id: REVISE_JOB, kind: 'revise_draft' }),
    })
    renderAt(PAGE)
    const box = await screen.findByLabelText('What should change?')
    fireEvent.change(box, { target: { value: '  Make the climb longer  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change it' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const call = api.to('POST', '/api/jobs')[0]!
    expect(call.body).toEqual({
      kind: 'revise_draft',
      input: {
        class_id: CLASS_ID,
        base_revision_id: REV1,
        request: 'Make the climb longer',
        mowl_connection_id: MOWL_CONNECTION,
      },
    })
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    await waitFor(() => {
      expect(box).toHaveValue('')
    })
    everyRequestStaysOnTheOrigin(api)
  })

  it('refuses an empty or too long request without sending it', async () => {
    const api = fakeApi({ ...SESSION_ROUTES, ...classRoutes(), 'GET /api/connections': MOWL })
    renderAt(PAGE)
    const box = await screen.findByLabelText('What should change?')
    fireEvent.click(screen.getByRole('button', { name: 'Change it' }))
    expect(await screen.findByText('Write what you want changed.')).toBeInTheDocument()
    fireEvent.change(box, { target: { value: 'x'.repeat(2001) } })
    fireEvent.click(screen.getByRole('button', { name: 'Change it' }))
    expect(await screen.findByText(/up to 2000 characters; this one has 2001/)).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('a stale tab (409 stale_base) shows the reason, keeps the text and reloads', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes(),
      'GET /api/connections': MOWL,
      'POST /api/jobs': () => reason(409, 'stale_base'),
    })
    renderAt(PAGE)
    const box = await screen.findByLabelText('What should change?')
    fireEvent.change(box, { target: { value: 'Make it harder' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change it' }))
    expect(await screen.findByText(/changed in another tab or window, so your request was not sent/)).toBeInTheDocument()
    await waitFor(() => {
      expect(api.to('GET', CLASS_PATH).length).toBeGreaterThanOrEqual(2)
      expect(api.to('GET', `${CLASS_PATH}/messages`).length).toBeGreaterThanOrEqual(2)
    })
    expect(box).toHaveValue('Make it harder')
    expect(api.to('POST', '/api/jobs')).toHaveLength(1)
  })

  it.each([
    ['revision_limit', /most changes one class can have/],
    ['chat_full', /conversation is full/],
    ['class_busy', /busy with another task/],
  ])('answers the reason %s in a plain sentence', async (code, sentence) => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes(),
      'GET /api/connections': MOWL,
      'POST /api/jobs': () => reason(409, code),
    })
    renderAt(PAGE)
    fireEvent.change(await screen.findByLabelText('What should change?'), { target: { value: 'Harder' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change it' }))
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    expect(screen.queryByText(code)).toBeNull()
  })

  it('does not send when MOWL is not connected', async () => {
    const api = fakeApi({ ...SESSION_ROUTES, ...classRoutes(), 'GET /api/connections': { connections: [] } })
    renderAt(PAGE)
    fireEvent.change(await screen.findByLabelText('What should change?'), { target: { value: 'Harder' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change it' }))
    expect(await screen.findByText(/MOWL is not connected/)).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })
})

describe('deleting a class', () => {
  it('warns that the saved MOWL workouts stay, then deletes', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: { saved: { count: 2, latest_revision_number: 1, latest_saved_at: '2026-10-29T11:00:00Z', latest_up_to_date: true } },
      }),
      [`DELETE ${CLASS_PATH}`]: { deleted: true, mowl_workouts_kept: 2, note: 'x' },
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete class' }))
    expect(screen.getByText(/The 2 workouts classbeat saved to your MOWL account stay there; delete them in MOWL/)).toBeInTheDocument()
    expect(api.to('DELETE', CLASS_PATH)).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, delete this class' }))
    expect(await screen.findByRole('heading', { name: 'Class deleted' })).toBeInTheDocument()
    expect(api.to('DELETE', CLASS_PATH)[0]?.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })

  it('asks again for an undecided write, then deletes with confirm_uncertain', async () => {
    let first = true
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes(),
      [`DELETE ${CLASS_PATH}`]: () => {
        if (first) {
          first = false
          return reason(409, 'uncertain_write')
        }
        return { deleted: true, mowl_workouts_kept: 0, note: 'x' }
      },
      [`DELETE ${CLASS_PATH}?confirm_uncertain=true`]: { deleted: true, mowl_workouts_kept: 0, note: 'x' },
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete class' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, delete this class' }))
    expect(await screen.findByText(/not sure whether a save of this class to MOWL went through/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Delete anyway' }))
    expect(await screen.findByRole('heading', { name: 'Class deleted' })).toBeInTheDocument()
    expect(api.to('DELETE', `${CLASS_PATH}?confirm_uncertain=true`)).toHaveLength(1)
  })

  it('shows why a busy class cannot be deleted and reloads', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes(),
      [`DELETE ${CLASS_PATH}`]: () => reason(409, 'class_busy'),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete class' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, delete this class' }))
    expect(await screen.findByText(/busy with another task/)).toBeInTheDocument()
    await waitFor(() => {
      expect(api.to('GET', CLASS_PATH).length).toBeGreaterThanOrEqual(2)
    })
  })
})

describe('deleting while a write may still land', () => {
  it('explains call_may_be_running, keeps the class and offers no confirm_uncertain', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes(),
      [`DELETE ${CLASS_PATH}`]: () => reason(409, 'call_may_be_running'),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete class' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, delete this class' }))
    expect(
      await screen.findByText(/may still be writing this class to MOWL.*only record of that write.*Wait a few minutes/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete anyway' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Yes, delete this class' })).toBeInTheDocument()
    expect(api.to('DELETE', `${CLASS_PATH}?confirm_uncertain=true`)).toHaveLength(0)
  })
})

describe('a class that is not there', () => {
  it('is the same page for an unknown and a foreign class', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${CLASS_PATH}`]: reason(404, 'not_found'),
      [`GET ${CLASS_PATH}/revisions`]: reason(404, 'not_found'),
      [`GET ${CLASS_PATH}/messages`]: reason(404, 'not_found'),
    })
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Class not found' })).toBeInTheDocument()
  })

  it('offers to try again when the class cannot be loaded', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${CLASS_PATH}`]: json({}, 500),
      [`GET ${CLASS_PATH}/revisions`]: json({}, 500),
      [`GET ${CLASS_PATH}/messages`]: json({}, 500),
    })
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'The class could not be loaded' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})
