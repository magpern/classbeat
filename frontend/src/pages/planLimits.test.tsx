import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CLASS_PATH, classRoutes } from '../test/classFixtures'
import { resetNudge } from '../lib/nudge'
import { fakeApi, json, PLAN_FREE } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { connectionsBody } from '../test/songFixtures'
import { renderAt } from '../test/render'

// M7a-4 (#949): limits that say why. The API is faked; nothing here reaches a network.

const FREE_PLAN = {
  ...PLAN_FREE,
  limits: { classes_per_month: 3, revisions_per_class: 3, concurrent_jobs: 1, max_class_minutes: 30 },
  gates: { games: false, races: true, ftp_tests: false },
}
const PAGE = `/classes/${CLASS_ID}`
const NO_VERSION = { current_revision_id: null, current_revision_number: null, revision_count: 0 }
const INTERPRETATION = `GET ${CLASS_PATH}/interpretation`
const DESIGN = `POST ${CLASS_PATH}/design-from-interpretation`

function handoffRoutes(items: unknown[], extra: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: NO_VERSION, revisions: [] }),
    'GET /api/account/plan': FREE_PLAN,
    [INTERPRETATION]: { refusal: null, design_job: null, items },
    [DESIGN]: { job_id: 'b1111111-1111-4111-8111-111111111111' },
    ...extra,
  }
}

const notAsked = (path: string, reason_code: string, value: unknown = null) => ({
  path,
  origin: 'not_as_asked',
  reason_code,
  value,
  quote: null,
})

afterEach(() => {
  sessionStorage.clear()
  resetNudge()
})

describe('an over-plan request waits for a choice before the design job', () => {
  it.each([
    ['over_plan_length', notAsked('length_min', 'over_plan_length', 60), /longer than your plan allows, which is 30 minutes/, 'Individual'],
    ['plan_no_games', notAsked('games.0', 'plan_no_games', { type: 'racetrack' }), /does not include games/, 'Individual'],
    ['plan_no_ftp_tests', notAsked('ftp_test', 'plan_no_ftp_tests', 20), /does not include FTP tests/, 'Individual'],
  ])('%s: explains it, offers the two ways and sends nothing', async (_code, item, reasonText, plan) => {
    const api = fakeApi(handoffRoutes([item]))
    renderAt(PAGE)
    const choice = await screen.findByTestId('plan-choice')
    expect(within(choice).getByText(reasonText)).toBeInTheDocument()
    expect(within(choice).getByText(`${plan} allows all of it.`)).toBeInTheDocument()
    expect(within(choice).getByRole('button', { name: 'Design within my plan' })).toBeInTheDocument()
    expect(within(choice).getByRole('link', { name: `See ${plan}` })).toHaveAttribute('href', '/en/account#plan')
    // The design hand-off (the job that takes the class credit) has not been sent, and no price is shown.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0)
    expect(choice).not.toHaveTextContent(/[$€£]|\bkr\b|per month|\/month/i)
  })

  it('names Professional when only it allows the length', async () => {
    fakeApi(handoffRoutes([notAsked('length_min', 'over_plan_length', 120)]))
    renderAt(PAGE)
    const choice = await screen.findByTestId('plan-choice')
    expect(within(choice).getByText('Professional allows all of it.')).toBeInTheDocument()
  })

  it('sends the hand-off once, and only after "Design within my plan"', async () => {
    const api = fakeApi(handoffRoutes([notAsked('length_min', 'over_plan_length', 60)]))
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Design within my plan' }))
    await waitFor(() => {
      expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1)
    })
    expect(screen.queryByTestId('plan-choice')).toBeNull()
  })

  it('"See plans" starts nothing and leaves the choice open', async () => {
    const api = fakeApi(handoffRoutes([notAsked('ftp_test', 'plan_no_ftp_tests', 20)]))
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('link', { name: 'See Individual' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/en/account')
    })
    expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(0)
  })

  it('does not hold a request with no plan reason: races and other limits go straight to the design', async () => {
    const api = fakeApi(handoffRoutes([notAsked('races', 'plan_no_races', 2), notAsked('sport', 'sport_not_offered', 'running')]))
    renderAt(PAGE)
    await waitFor(() => {
      expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1)
    })
    expect(screen.queryByTestId('plan-choice')).toBeNull()
  })

  it('does not hold a request that was kept within the plan (an "As you asked" item with the same path)', async () => {
    const api = fakeApi(handoffRoutes([{ path: 'length_min', origin: 'explicit', reason_code: null, value: 30, quote: null }]))
    renderAt(PAGE)
    await waitFor(() => {
      expect(api.to('POST', `${CLASS_PATH}/design-from-interpretation`)).toHaveLength(1)
    })
  })
})

const INTERPRET_JOB = jobBody('queued', { kind: 'interpret_request' })

function startRoutes(plan: object, extra: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    'GET /api/connections': connectionsBody(false),
    'GET /api/music/mowl-playlists': { playlists: [] },
    'GET /api/account/plan': plan,
    'POST /api/jobs': () => json(INTERPRET_JOB, 201),
    ...extra,
  }
}

const refuse = (detail: Record<string, unknown>) => ({
  'POST /api/jobs': () => json({ detail: { reason: 'quota', ...detail } }, 409),
})

async function press() {
  fireEvent.change(await screen.findByLabelText('Describe your class'), { target: { value: 'Ninety minutes with games' } })
  fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
}

describe('the new class screen', () => {
  it("shows the plan's longest class near the prompt, from the account plan", async () => {
    fakeApi(startRoutes(FREE_PLAN))
    renderAt('/classes/new')
    expect(await screen.findByTestId('plan-length')).toHaveTextContent('Your plan allows classes of up to 30 minutes.')
  })

  it.each([
    [{ quota: 'length', limit: 30 }, /allows classes of up to 30 minutes\. Choose a shorter length\./],
    [{ quota: 'feature', feature: 'games' }, /does not include games/],
    [{ quota: 'classes', limit: 3, used: 3, period_end: '2026-11-01T00:00:00Z' }, /used your 3 classes for October/],
  ])('a 409 quota %j shows its wording with the upgrade link', async (detail, sentence) => {
    fakeApi(startRoutes(FREE_PLAN, refuse(detail)))
    renderAt('/classes/new')
    await press()
    expect(await screen.findByRole('alert')).toHaveTextContent(sentence)
    const upgrade = await screen.findByTestId('upgrade')
    expect(within(upgrade).getByRole('link', { name: 'See plans' })).toHaveAttribute('href', '/en/account#plan')
    expect(upgrade).not.toHaveTextContent(/[$€£]|\bkr\b/)
  })

  it.each([
    [{ quota: 'cost', period_end: '2026-11-01T00:00:00Z' }],
    [{ quota: 'feature', feature: 'races' }],
  ])('a 409 quota %j that no plan would lift shows no upgrade prompt and no nudge', async (detail) => {
    fakeApi(startRoutes(FREE_PLAN, refuse(detail)))
    renderAt('/classes/new')
    await press()
    await screen.findByRole('alert')
    expect(screen.queryByTestId('upgrade')).toBeNull()
    expect(screen.queryByTestId('plan-nudge')).toBeNull()
  })

  it('a stale-page race (409 class_busy) is not an upgrade prompt', async () => {
    fakeApi(startRoutes(FREE_PLAN, { 'POST /api/jobs': () => json({ detail: { reason: 'class_busy' } }, 409) }))
    renderAt('/classes/new')
    await press()
    await screen.findByRole('alert')
    expect(screen.queryByTestId('upgrade')).toBeNull()
  })

  it('works the same on the advanced form', async () => {
    fakeApi(
      startRoutes(FREE_PLAN, {
        'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: 'AbCdEfGhIjKlMnOpQrStUv', name: 'Hills' }] },
        ...refuse({ quota: 'length', limit: 30 }),
      }),
    )
    renderAt('/classes/new/advanced')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Long ride' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Hills' }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByTestId('upgrade')).toBeInTheDocument()
  })
})

const INDIVIDUAL_PLAN = { ...PLAN_FREE, plan: 'instructor', name: 'Individual' }
const classesLeft = (left: number) => ({ ...FREE_PLAN, classes_held: 3 - left })
const nudge = () => screen.queryByTestId('plan-nudge')

describe('the gentle nudge toward Individual', () => {
  it('shows when one class is left this month, without a price, a countdown or a modal', async () => {
    fakeApi(startRoutes(classesLeft(1)))
    renderAt('/classes/new')
    const box = await screen.findByTestId('plan-nudge')
    expect(box).toHaveTextContent('Individual gives you 9 classes a month and classes of up to 75 minutes.')
    expect(box).not.toHaveTextContent(/[$€£]|\bkr\b|\d+ (days?|hours?) left|only|hurry|last chance/i)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(within(box).getByRole('link', { name: 'See Individual' })).toHaveAttribute('href', '/en/account#plan')
    // Work is never in its way.
    expect(screen.getByRole('button', { name: 'Design my class' })).toBeEnabled()
  })

  it('is quiet with more than one class left, with none left, and on a paid plan', async () => {
    for (const plan of [classesLeft(2), classesLeft(0), { ...INDIVIDUAL_PLAN, classes_held: 8 }]) {
      fakeApi(startRoutes(plan))
      const view = renderAt('/classes/new')
      await screen.findByLabelText('Describe your class')
      await new Promise((resolve) => setTimeout(resolve, 30))
      expect(nudge()).toBeNull()
      view.unmount()
      resetNudge()
    }
  })

  it('shows on the first refusal of the session only', async () => {
    fakeApi(startRoutes(FREE_PLAN, refuse({ quota: 'classes', limit: 3, used: 3, period_end: '2026-11-01T00:00:00Z' })))
    renderAt('/classes/new')
    await press()
    expect(await screen.findByTestId('plan-nudge')).toBeInTheDocument()
    // The second refusal still says why, with the link, but the nudge is not offered again.
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(nudge()).toBeNull()
    })
    expect(screen.getByTestId('upgrade')).toBeInTheDocument()
  })

  it('is not shown on a refusal for a paid plan', async () => {
    fakeApi(startRoutes(INDIVIDUAL_PLAN, refuse({ quota: 'length', limit: 75 })))
    renderAt('/classes/new')
    await press()
    expect(await screen.findByTestId('upgrade')).toBeInTheDocument()
    expect(nudge()).toBeNull()
  })

  it('shows once on the account page for a Free user', async () => {
    fakeApi(startRoutes(FREE_PLAN))
    renderAt('/account#plan')
    expect(await screen.findByTestId('plan-nudge')).toBeInTheDocument()
  })

  it('stays away after "Not now": no trigger shows it again in the session', async () => {
    fakeApi(startRoutes(classesLeft(1), refuse({ quota: 'classes', limit: 3, used: 3 })))
    renderAt('/classes/new')
    fireEvent.click(await screen.findByRole('button', { name: 'Not now' }))
    expect(nudge()).toBeNull()
    await press()
    expect(await screen.findByTestId('upgrade')).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(nudge()).toBeNull()
  })
})
