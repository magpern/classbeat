import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, MOWL_CONNECTION, classBody, message } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import { SPOTIFY_ID } from '../test/songFixtures'
import { renderAt } from '../test/render'

// The reading window (ADR 0038 section 2, #1206): the class page of a composer class shows the
// workspace from the first second, while the reading runs and no version exists yet.

const PAGE = `/classes/${CLASS_ID}`
const OK = { status: 'connected', last_error: null, connected_at: '2026-10-28T10:00:00Z', last_used_at: null }
const CONNECTIONS = {
  connections: [
    { id: MOWL_CONNECTION, provider: 'mowl', ...OK },
    { id: SPOTIFY_ID, provider: 'spotify', ...OK },
  ],
  composer_flow: true,
}
const READING = { id: JOB_ID, kind: 'interpret_request', state: 'running' }

/** A class in its reading window: no version, the reading job open, no reading stored. */
function setup(over: { cls?: Record<string, unknown>; flag?: boolean } = {}) {
  return fakeApi({
    ...SESSION_ROUTES,
    'GET /api/connections': { ...CONNECTIONS, composer_flow: over.flag ?? true },
    [`GET ${CLASS_PATH}`]: () =>
      classBody({
        sport: 'cycling',
        composer: true,
        current_revision_id: null,
        current_revision_number: null,
        revision_count: 0,
        profile: [],
        open_job_id: JOB_ID,
        open_job: READING,
        ...over.cls,
      }),
    [`GET ${CLASS_PATH}/revisions`]: { revisions: [] },
    [`GET ${CLASS_PATH}/messages`]: {
      messages: [message(1, 'user', 'Forty-five minutes, three climbs', { job_id: JOB_ID, base_revision_id: null })],
    },
    [`GET ${CLASS_PATH}/interpretation`]: () => reason(409, 'not_ready'),
    [`GET ${CLASS_PATH}/pending-messages`]: () => json({ messages: [] }),
    [`GET /api/jobs/${JOB_ID}`]: () => json(jobBody('running', { kind: 'interpret_request' })),
    [`GET /api/jobs/${JOB_ID}/events`]: () => openStream(),
  })
}

describe('the composer workspace while the request is being read', () => {
  it('shows the workspace with the request, the reading status and no sketch', async () => {
    const api = setup()
    renderAt(PAGE)
    expect(await screen.findByTestId('composer-workspace')).toBeInTheDocument()
    expect(screen.getByTestId('request-block')).toHaveTextContent('Forty-five minutes, three climbs')
    expect(await screen.findByTestId('job-status')).toHaveTextContent('Reading your request…')
    expect(screen.getByText('No sketch yet')).toBeInTheDocument()
    expect(screen.queryByText('Rough: songs not in yet')).toBeNull()
    expect(screen.queryByTestId('reading-card')).toBeNull()
    expect(screen.getByText('The class has no version to change yet.')).toBeInTheDocument()
    // The driver does nothing while the reading runs: no fill, no submit.
    await waitFor(() => {
      expect(api.to('GET', `${CLASS_PATH}/interpretation`).length).toBeGreaterThan(0)
    })
    expect(api.calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  it('keeps the workspace, with the paused notice, when the flag is off for the browser', async () => {
    setup({ flag: false })
    renderAt(PAGE)
    expect(await screen.findByTestId('composer-workspace')).toBeInTheDocument()
    expect((await screen.findAllByText(/The composer is paused here right now/)).length).toBeGreaterThan(0)
    expect(screen.queryByText('This class has no version yet.')).toBeNull()
  })

  it('keeps the old class page for a class the answer says is not a composer class', async () => {
    setup({ cls: { composer: false } })
    renderAt(PAGE)
    await screen.findByText('This class has no version yet.')
    expect(screen.queryByTestId('composer-workspace')).toBeNull()
  })
})
