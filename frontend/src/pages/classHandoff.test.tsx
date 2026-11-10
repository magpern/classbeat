import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CLASS_PATH, REVISE_JOB, classRoutes } from '../test/classFixtures'
import { fakeApi, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// MA2 (#882) opened the hand-off from a class started on the start screen; MA3 (#883) draws it in
// the class workspace: no redirect, the designing state in place, a refusal with its way out.

const DESIGN_JOB = REVISE_JOB
const PAGE = `/classes/${CLASS_ID}`
const NO_VERSION = { current_revision_id: null, current_revision_number: null, revision_count: 0 }
const INTERPRETATION = `GET ${CLASS_PATH}/interpretation`
const DESIGN = `POST ${CLASS_PATH}/design-from-interpretation`
const AGAIN = `POST ${CLASS_PATH}/interpret-again`
const NEW_CLASS = '55555555-5555-4555-8555-555555555555'
const NEW_JOB = '66666666-6666-4666-8666-666666666666'
const DESIGNING = { id: DESIGN_JOB, kind: 'design_draft', state: 'running' }

function routes(extra: Record<string, unknown>, cls: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { ...NO_VERSION, ...cls }, revisions: [] }),
    ...extra,
  }
}

const designJob = {
  [`GET /api/jobs/${DESIGN_JOB}`]: jobBody('running', { id: DESIGN_JOB }),
  [`GET /api/jobs/${DESIGN_JOB}/events`]: () => openStream(),
}

afterEach(() => sessionStorage.clear())

describe('the hand-off from the start screen', () => {
  it('asks for the design once the reading is stored and shows the design in place', async () => {
    let started = false
    const api = fakeApi(
      routes({
        [INTERPRETATION]: { refusal: null, design_job: null, items: [] },
        [DESIGN]: () => {
          started = true
          return { job_id: DESIGN_JOB }
        },
        [`GET ${CLASS_PATH}`]: () =>
          started
            ? { ...classRoutes({ cls: { ...NO_VERSION, open_job: DESIGNING } })[`GET ${CLASS_PATH}`] }
            : classRoutes({ cls: NO_VERSION })[`GET ${CLASS_PATH}`],
        ...designJob,
      }),
    )
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
    expect(window.location.pathname).toBe(`/en${PAGE}`)
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1)
  })

  it('asks for the design again on reopening when the server has none (the call is idempotent)', async () => {
    const api = fakeApi(routes({ [INTERPRETATION]: { refusal: null, design_job: null, items: [] }, [DESIGN]: { job_id: DESIGN_JOB } }))
    renderAt(PAGE)
    await waitFor(() => expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1))
  })

  it('does not ask again for a design job that already exists', async () => {
    const api = fakeApi(
      routes(
        { [INTERPRETATION]: { refusal: null, design_job: { job_id: DESIGN_JOB }, items: [] }, ...designJob },
        { open_job: DESIGNING },
      ),
    )
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0)
  })

  it('says a refused request could not be turned into a class and offers Try again', async () => {
    fakeApi(routes({ [INTERPRETATION]: { refusal: { reason: 'out_of_scope' }, design_job: null, items: [] } }))
    renderAt(PAGE)
    expect(await screen.findByText(/could not be turned into a class as asked/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Try again with the same request' })).toHaveAttribute(
      'href',
      `/en/classes/new?from=${CLASS_ID}`,
    )
    expect(screen.queryByRole('button', { name: 'Interpret again' })).toBeNull()
  })

  it('offers Interpret again after a stored not_connected refusal and opens the new class', async () => {
    sessionStorage.setItem(
      `classbeat.start-request.${CLASS_ID}`,
      JSON.stringify({ request: 'A hilly ride', music: null, sport: 'cycling' }),
    )
    const api = fakeApi(
      routes({
        [INTERPRETATION]: { refusal: { reason: 'not_connected' }, design_job: null, items: [] },
        [AGAIN]: { job_id: NEW_JOB },
        [`GET /api/jobs/${NEW_JOB}`]: jobBody('queued', { id: NEW_JOB, kind: 'interpret_request', class_id: NEW_CLASS }),
      }),
    )
    renderAt(PAGE)
    expect(await screen.findByText(/MOWL was not connected/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Interpret again' }))
    await waitFor(() => expect(window.location.pathname).toBe(`/en/classes/${NEW_CLASS}`))
    expect(api.to('POST', `${CLASS_PATH}/interpret-again`)).toHaveLength(1)
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0)
    // The request is kept for the new class, so its own Try again still has it.
    expect(sessionStorage.getItem(`classbeat.start-request.${NEW_CLASS}`)).toContain('A hilly ride')
  })

  it('offers Interpret again when the hand-off call itself is refused with not_connected', async () => {
    fakeApi(
      routes({
        [INTERPRETATION]: { refusal: null, design_job: null, items: [] },
        [DESIGN]: () => reason(409, 'not_connected'),
      }),
    )
    renderAt(PAGE)
    expect(await screen.findByText(/MOWL was not connected/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Interpret again' })).toBeInTheDocument()
  })

  it('says the reading failed when it is not stored, no job runs and the request was kept', async () => {
    sessionStorage.setItem(
      `classbeat.start-request.${CLASS_ID}`,
      JSON.stringify({ request: 'A hilly ride', music: null, sport: 'cycling' }),
    )
    fakeApi(routes({ [INTERPRETATION]: () => reason(409, 'not_ready') }))
    renderAt(PAGE)
    expect(await screen.findByText('Your request could not be read.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Try again with the same request' })).toBeInTheDocument()
  })

  it('stays quiet for a class that was not made on the start screen', async () => {
    fakeApi(routes({ [INTERPRETATION]: () => reason(409, 'not_ready') }))
    renderAt(PAGE)
    await screen.findByText(/This class has no version yet/)
    await waitFor(() => expect(screen.queryByText('Reading your request.')).toBeNull())
    expect(screen.queryByText('Your request could not be read.')).toBeNull()
  })

  it('waits while the reading job runs and says so in the status pill', async () => {
    fakeApi(
      routes(
        { [INTERPRETATION]: () => reason(409, 'not_ready') },
        { open_job_id: DESIGN_JOB, open_job: { id: DESIGN_JOB, kind: 'interpret_request', state: 'running' } },
      ),
    )
    renderAt(PAGE)
    expect(await screen.findByText('Reading your request.')).toBeInTheDocument()
    expect(screen.getByTestId('class-header')).toHaveTextContent('Reading your request')
  })
})
