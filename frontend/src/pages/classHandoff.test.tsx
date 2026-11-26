import { fireEvent, screen, waitFor, within } from '@testing-library/react'
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
const READING = 'd1111111-1111-4111-8111-111111111111'
const READ_AGAIN = 'd2222222-2222-4222-8222-222222222222'
const REREAD = `POST ${CLASS_PATH}/reread`
/** A stored reading: the one the confirm screen shows. */
const reading = (extra: Record<string, unknown> = {}) => ({
  interpret_job_id: READING,
  refusal: null,
  design_job: null,
  items: [],
  ...extra,
})
const LENGTH_Q = { topic: 'length', text: 'How long should it be?', options: ['30 minutes', '45 minutes', '60 minutes'], assumed_index: 1 }
const confirmDesign = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Design this class' }))
}

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
  it('asks for nothing before the confirmation, then for the design once, and shows the design in place', async () => {
    let started = false
    const api = fakeApi(
      routes({
        [INTERPRETATION]: reading(),
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
    await screen.findByRole('button', { name: 'Design this class' })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0) // nothing before the confirmation
    await confirmDesign()
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
    expect(window.location.pathname).toBe(`/en${PAGE}`)
    const asked = api.to('POST', `${CLASS_PATH}/design-from-interpretation`)
    expect(asked).toHaveLength(1)
    expect(asked[0]?.body).toEqual({ interpret_job_id: READING }) // the reading the instructor confirmed
  })

  it('waits for the confirmation again on reopening (the hand-off itself stays idempotent)', async () => {
    const api = fakeApi(routes({ [INTERPRETATION]: reading(), [DESIGN]: { job_id: DESIGN_JOB } }))
    const first = renderAt(PAGE)
    await screen.findByRole('button', { name: 'Design this class' })
    first.unmount()
    renderAt(PAGE)
    await screen.findByRole('button', { name: 'Design this class' })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0)
    await confirmDesign()
    await waitFor(() => expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1))
  })

  it('reads the request again for a changed answer, not the hand-off, and asks the new reading', async () => {
    let reread = false
    const api = fakeApi(
      routes({
        [INTERPRETATION]: () =>
          reread
            ? reading({ interpret_job_id: READ_AGAIN, questions: [] })
            : reading({ questions: [LENGTH_Q] }),
        [REREAD]: () => {
          reread = true
          return { job_id: READ_AGAIN }
        },
        [`GET /api/jobs/${READ_AGAIN}`]: jobBody('completed', { id: READ_AGAIN, kind: 'interpret_request' }),
        [DESIGN]: { job_id: DESIGN_JOB },
      }),
    )
    renderAt(PAGE)
    const group = await screen.findByRole('group', { name: 'How long should it be?' })
    expect(within(group).getByRole('radio', { name: /45 minutes/ })).toBeChecked() // the assumed answer, pre-selected
    fireEvent.click(within(group).getByRole('radio', { name: /60 minutes/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Update the reading' }))
    await waitFor(() => expect(api.to('POST', `${CLASS_PATH}/reread`)).toHaveLength(1))
    expect(api.to('POST', `${CLASS_PATH}/reread`)[0]?.body).toEqual({
      interpret_job_id: READING,
      answers: [{ question_index: 0, option_index: 2 }],
    })
    // The new reading is shown for confirmation: still nothing designed, and the hand-off names it.
    await waitFor(() => expect(screen.queryByRole('group', { name: 'How long should it be?' })).toBeNull())
    await confirmDesign()
    await waitFor(() => expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1))
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)[0]?.body).toEqual({ interpret_job_id: READ_AGAIN })
  })

  it('shows the reading state while the new reading is made, then the earlier reading when the re-read failed', async () => {
    let jobState = 'running'
    fakeApi(
      routes({
        [INTERPRETATION]: reading({ questions: [LENGTH_Q] }),
        [REREAD]: { job_id: READ_AGAIN },
        [`GET /api/jobs/${READ_AGAIN}`]: () => jobBody(jobState, { id: READ_AGAIN, kind: 'interpret_request' }),
      }),
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('radio', { name: /60 minutes/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Update the reading' }))
    expect(await screen.findByText('Reading your request.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'How long should it be?' })).toBeNull() // the old questions are not asked
    jobState = 'failed'
    expect(await screen.findByText(/could not be updated/, undefined, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'How long should it be?' })).toBeInTheDocument() // the earlier reading stands
  }, 10_000)

  it('does not ask again for a design job that already exists', async () => {
    const api = fakeApi(
      routes(
        { [INTERPRETATION]: reading({ design_job: { job_id: DESIGN_JOB } }), ...designJob },
        { open_job: DESIGNING },
      ),
    )
    renderAt(PAGE)
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0)
  })

  it('says a refusal with an unlisted reason did not start, blames nothing, and offers the start screen', async () => {
    fakeApi(routes({ [INTERPRETATION]: reading({ refusal: { reason: 'out_of_scope' } }) }))
    renderAt(PAGE)
    expect(await screen.findByText('classbeat could not start this design. Nothing was charged and your request is kept.')).toBeInTheDocument()
    expect(screen.queryByText(/as asked/)).toBeNull()
    expect(screen.getByRole('link', { name: 'Try again with the same request' })).toHaveAttribute(
      'href',
      `/en/classes/new?from=${CLASS_ID}`,
    )
    expect(screen.queryByRole('button', { name: 'Interpret again' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it('offers Interpret again after a stored not_connected refusal and opens the new class', async () => {
    sessionStorage.setItem(
      `classbeat.start-request.${CLASS_ID}`,
      JSON.stringify({ request: 'A hilly ride', music: null, sport: 'cycling' }),
    )
    const api = fakeApi(
      routes({
        [INTERPRETATION]: reading({ refusal: { reason: 'not_connected' } }),
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
        [INTERPRETATION]: reading(),
        [DESIGN]: () => reason(409, 'not_connected'),
      }),
    )
    renderAt(PAGE)
    await confirmDesign()
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
