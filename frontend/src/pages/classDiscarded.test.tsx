import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CLASS_PATH, classRoutes } from '../test/classFixtures'
import { fakeApi, json, reason, type Handler } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { renderAt } from '../test/render'
import { rememberRequest } from '../lib/startRequest'

// ADR 0039: a class whose first creation failed or was stopped is removed by the server, which keeps
// the ended job. The class page says why and offers the request again, never a bare "not found".

const PAGE = `/classes/${CLASS_ID}`
const NO_VERSION = { current_revision_id: null, current_revision_number: null, revision_count: 0 }
const GONE = () => json({ detail: { reason: 'class_discarded', job_id: JOB_ID } }, 404)
const READING = { id: JOB_ID, kind: 'interpret_request', state: 'running' }

function gone(job: Handler | object) {
  return {
    ...SESSION_ROUTES,
    [`GET ${CLASS_PATH}`]: GONE,
    [`GET ${CLASS_PATH}/revisions`]: () => reason(404, 'not_found'),
    [`GET ${CLASS_PATH}/messages`]: () => reason(404, 'not_found'),
    [`GET /api/jobs/${JOB_ID}`]: job,
  }
}

afterEach(() => sessionStorage.clear())

describe('a class its failed first creation removed', () => {
  it('says why it stopped and offers the same request again', async () => {
    rememberRequest(CLASS_ID, { request: 'A 30 minute climb', music: null, sport: 'cycling' })
    fakeApi(gone(jobBody('failed', { kind: 'interpret_request', error_category: 'model_refused' })))
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Class not kept' })).toBeInTheDocument()
    expect(await screen.findByText(/The designer declined this brief/)).toBeInTheDocument()
    expect(screen.getByText('It never got a version, so the empty class was removed.')).toBeInTheDocument()
    const again = screen.getByRole('link', { name: 'Try again with the same request' })
    expect(again.getAttribute('href')).toContain(`/classes/new?from=${CLASS_ID}`)
    expect(screen.queryByText('Class not found')).toBeNull()
  })

  it('says a stopped design was stopped and offers a new class when no request is kept', async () => {
    fakeApi(gone(jobBody('cancelled', { kind: 'design_draft' })))
    renderAt(PAGE)
    expect(await screen.findByText('The design was stopped.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Design a class' }).getAttribute('href')).toContain('/classes/new')
    expect(screen.queryByRole('link', { name: 'Try again with the same request' })).toBeNull()
  })

  it('still says the class is gone when its job cannot be read', async () => {
    fakeApi(gone(() => reason(503, 'unavailable')))
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Class not kept' })).toBeInTheDocument()
    expect(screen.getByText('It never got a version, so the empty class was removed.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('replaces the reading in progress once the class is gone', async () => {
    rememberRequest(CLASS_ID, { request: 'A 30 minute climb', music: null, sport: 'cycling' })
    let reads = 0
    const shell = classRoutes({ cls: { ...NO_VERSION, open_job_id: JOB_ID, open_job: READING }, revisions: [] })
    fakeApi({
      ...SESSION_ROUTES,
      ...shell,
      [`GET ${CLASS_PATH}`]: () => (++reads === 1 ? shell[`GET ${CLASS_PATH}`] : GONE()),
      [`GET ${CLASS_PATH}/interpretation`]: () => reason(409, 'not_ready'),
      [`GET /api/jobs/${JOB_ID}`]: jobBody('failed', { kind: 'interpret_request', error_category: 'model_refused' }),
    })
    renderAt(PAGE)
    expect(await screen.findByText('Reading your request.')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Class not kept' }, { timeout: 5000 })).toBeInTheDocument()
    expect(await screen.findByText(/The designer declined this brief/)).toBeInTheDocument()
  })

  it('keeps a plain not found for any other missing class', async () => {
    fakeApi({ ...SESSION_ROUTES, [`GET ${CLASS_PATH}`]: () => reason(404, 'not_found') })
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Class not found' })).toBeInTheDocument()
  })
})
