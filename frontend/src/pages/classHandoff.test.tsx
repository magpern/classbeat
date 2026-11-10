import { screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CLASS_PATH, classRoutes } from '../test/classFixtures'
import { fakeApi, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// MA2 (#882): the minimal hand-off from a class started on the start screen to its design job.

const DESIGN_JOB = 'd1111111-1111-4111-8111-111111111111'
const PAGE = `/classes/${CLASS_ID}`
const NO_VERSION = { current_revision_id: null, current_revision_number: null, revision_count: 0 }
const INTERPRETATION = `GET ${CLASS_PATH}/interpretation`
const DESIGN = `POST ${CLASS_PATH}/design-from-interpretation`

function routes(extra: Record<string, unknown>, cls: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { ...NO_VERSION, ...cls }, revisions: [] }),
    ...extra,
  }
}

afterEach(() => sessionStorage.clear())

describe('the hand-off from the start screen', () => {
  it('asks for the design once the reading is stored and follows the design job', async () => {
    const api = fakeApi(
      routes({
        [INTERPRETATION]: { refusal: null, design_job: null },
        [DESIGN]: { job_id: DESIGN_JOB },
      }),
    )
    renderAt(PAGE)
    await waitFor(() => expect(window.location.pathname).toBe(`/en/jobs/${DESIGN_JOB}`))
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1)
  })

  it('follows a design job that already exists without asking again', async () => {
    const api = fakeApi(
      routes({
        [INTERPRETATION]: { refusal: null, design_job: { job_id: DESIGN_JOB } },
      }),
    )
    renderAt(PAGE)
    await waitFor(() => expect(window.location.pathname).toBe(`/en/jobs/${DESIGN_JOB}`))
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0)
  })

  it('says a refused request could not be turned into a class and offers Try again', async () => {
    fakeApi(routes({ [INTERPRETATION]: { refusal: { reason: 'out_of_scope' }, design_job: null } }))
    renderAt(PAGE)
    expect(await screen.findByText(/could not be turned into a class as asked/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Try again with the same request' })).toHaveAttribute(
      'href',
      `/en/classes/new?from=${CLASS_ID}`,
    )
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

  it('waits while the reading job runs', async () => {
    fakeApi(
      routes(
        { [INTERPRETATION]: () => reason(409, 'not_ready') },
        { open_job_id: DESIGN_JOB, open_job: { id: DESIGN_JOB, kind: 'interpret_request', state: 'running' } },
      ),
    )
    renderAt(PAGE)
    expect(await screen.findByText('Reading your request.')).toBeInTheDocument()
  })
})

