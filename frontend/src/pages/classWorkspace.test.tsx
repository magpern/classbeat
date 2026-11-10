import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  CLASS_PATH,
  MOWL_CONNECTION,
  REV1,
  REVISE_JOB,
  classRoutes,
  message,
  revisionBody,
} from '../test/classFixtures'
import { fakeApi } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// MA3 (#883): the two-panel workspace of /classes/<id>: Chat on the left, Class on the right,
// tabs on a phone. The layout is CSS only, so the same regions are in the page at every width.

const PAGE = `/classes/${CLASS_ID}`
const INTERPRETATION = `GET ${CLASS_PATH}/interpretation`
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

const ITEMS = [
  { path: 'length_min', origin: 'explicit', value: 45, quote: 'a 45 minute ride' },
  { path: 'climbs', origin: 'explicit', value: 2 },
  { path: 'games.0', origin: 'not_as_asked', reason_code: 'plan_no_games', value: { type: 'racetrack' } },
  { path: 'intensity', origin: 'inferred', value: 'moderate' },
  { path: 'music', origin: 'default', value: 'first_playlist' },
]

const REQUEST = message(1, 'user', 'A 45 minute ride <b>with</b> two climbs', {
  job_id: REVISE_JOB,
  base_revision_id: null,
})

function withInterpretation(items: unknown[] | null) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({
      messages: [REQUEST],
      revisionBodies: {
        [REV1]: revisionBody(REV1, 1, undefined, {
          provenance: { origin: 'design_draft', ...(items === null ? {} : { interpretation: items }) },
        }),
      },
    }),
  }
}

describe('the class workspace', () => {
  it('has a labelled Chat region and a labelled Class region, with the page title above both', async () => {
    fakeApi(withInterpretation(ITEMS))
    renderAt(PAGE)
    const chat = await screen.findByRole('region', { name: 'Chat' })
    const work = screen.getByRole('region', { name: 'Class' })
    expect(within(chat).getByRole('heading', { name: 'Your request' })).toBeInTheDocument()
    expect(within(chat).getByLabelText('What should change?')).toBeInTheDocument()
    expect(within(work).getByRole('heading', { name: 'Versions' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })

  it('shows the request as plain text once, and not again in the change history', async () => {
    fakeApi(withInterpretation(ITEMS))
    renderAt(PAGE)
    const block = await screen.findByTestId('request-block')
    expect(within(block).getByText('A 45 minute ride <b>with</b> two climbs')).toBeInTheDocument()
    expect(screen.getAllByText(/A 45 minute ride/)).toHaveLength(1)
    expect(screen.queryByText('This request did not finish. Nothing changed.')).toBeNull()
  })

  it('tells the reading in three parts, worded from the catalogue', async () => {
    fakeApi(withInterpretation(ITEMS))
    renderAt(PAGE)
    const asked = await screen.findByRole('region', { name: 'As you asked' })
    expect(within(asked).getByText('Length: 45 minutes')).toBeInTheDocument()
    expect(within(asked).getByText('Your words: a 45 minute ride')).toBeInTheDocument()
    expect(within(asked).getByText('Climbs: 2')).toBeInTheDocument()
    const not = screen.getByRole('region', { name: 'Not as asked' })
    expect(within(not).getByText(/Racetrack\. Your plan does not include games\./)).toBeInTheDocument()
    const assumed = screen.getByRole('region', { name: 'Assumed' })
    expect(within(assumed).getByText('Effort: Moderate')).toBeInTheDocument()
    expect(within(assumed).getByText('Music: The first playlist in your MOWL music')).toBeInTheDocument()
  })

  it('says nothing about a reading for a version made before readings were kept', async () => {
    fakeApi(withInterpretation(null))
    renderAt(PAGE)
    await screen.findByRole('region', { name: 'Chat' })
    expect(screen.queryByTestId('interpretation')).toBeNull()
  })

  it('reads the reading from the class when there is no version yet', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: { current_revision_id: null, current_revision_number: null, revision_count: 0 },
        revisions: [],
        messages: [REQUEST],
      }),
      [INTERPRETATION]: { interpret_job_id: REVISE_JOB, refusal: { reason: 'out_of_scope' }, design_job: null, items: ITEMS },
    })
    renderAt(PAGE)
    const asked = await screen.findByRole('region', { name: 'As you asked' })
    expect(within(asked).getByText('Climbs: 2')).toBeInTheDocument()
    expect(await screen.findByText(/could not be turned into a class as asked/)).toBeInTheDocument()
  })

  it('has Chat and Class tabs for a phone; the class opens first and Chat swaps the panels', async () => {
    fakeApi(withInterpretation(ITEMS))
    renderAt(PAGE)
    const chatTab = await screen.findByRole('tab', { name: 'Chat' })
    const classTab = screen.getByRole('tab', { name: 'Class' })
    expect(classTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('ws-chat')).toHaveClass('max-lg:hidden')
    expect(screen.getByTestId('ws-class')).not.toHaveClass('max-lg:hidden')
    fireEvent.click(chatTab)
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('ws-class')).toHaveClass('max-lg:hidden')
    expect(screen.getByTestId('ws-chat')).not.toHaveClass('max-lg:hidden')
    // The tabs are for a phone only: from 1024 px both panels are side by side.
    expect(screen.getByRole('tablist', { name: 'Class workspace' })).toHaveClass('lg:hidden')
    fireEvent.keyDown(chatTab, { key: 'ArrowRight' })
    expect(classTab).toHaveAttribute('aria-selected', 'true')
    expect(classTab).toHaveFocus()
  })

  it('opens on Chat for a class with no version and no design yet', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: { current_revision_id: null, current_revision_number: null, revision_count: 0 },
        revisions: [],
      }),
    })
    renderAt(PAGE)
    expect(await screen.findByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true')
  })

  it('writes nothing to MOWL until the confirmation is answered', async () => {
    const api = fakeApi({ ...withInterpretation(ITEMS), 'GET /api/connections': MOWL })
    renderAt(PAGE)
    const save = await screen.findByRole('button', { name: 'Save to MOWL' })
    expect(save).toBeEnabled()
    const writes = () => api.calls.filter((c) => c.method !== 'GET')
    expect(writes()).toHaveLength(0)
    fireEvent.click(save)
    const confirm = await screen.findByRole('group', { name: 'Confirm' })
    expect(confirm).toBeInTheDocument()
    expect(writes()).toHaveLength(0)
    // The save bar sits at the foot of a phone screen: the same buttons, not a second set.
    expect(screen.getAllByRole('button', { name: 'Save to MOWL' })).toHaveLength(1)
  })
})
