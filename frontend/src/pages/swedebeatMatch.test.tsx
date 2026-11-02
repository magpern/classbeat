import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { timing } from '../components/swedebeat/NextStep'
import { classBody, classRoutes, REV1 } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { renderAt } from '../test/render'
import { HASH, LIST_ID, LIST_PATH, MOWL_ID, NEW_JOB, SPOTIFY_ID, listBody } from '../test/songFixtures'

const PAGE = `/classes/${CLASS_ID}`
const CLASS_URL = `/api/classes/${CLASS_ID}`
const MATCH_ID = '77777777-7777-4777-8777-777777777777'
const SWEDE_ID = 'c0000000-0000-4000-8000-000000000003'
const MATCH_PATH = `${CLASS_URL}/revisions/${MATCH_ID}`
const HANDOFF = `${CLASS_URL}/swedebeat-handoff`
const SONGS = { state: 'approved', job_id: null, revision_id: LIST_ID, content_hash: HASH, expires_at: null }
const PLAYLIST = { name: 'Hills <b>mix</b>', link: null }

function connections(swedebeatStatus: 'connected' | 'needs_reconnect' | null = 'connected') {
  const base = { last_error: null, connected_at: '2026-10-28T10:00:00Z', last_used_at: null }
  return {
    connections: [
      { id: MOWL_ID, provider: 'mowl', status: 'connected', ...base },
      { id: SPOTIFY_ID, provider: 'spotify', status: 'connected', ...base },
      ...(swedebeatStatus === null
        ? []
        : [{ id: SWEDE_ID, provider: 'swedebeat', status: swedebeatStatus, account: 'a@example.test', ...base }]),
    ],
  }
}

function song(position: number, status: 'found' | 'unsure' | 'missing', extra: Record<string, unknown> = {}) {
  return {
    position,
    status,
    wanted: { title: `Wanted ${String(position)} <i>x</i>`, artist: `Artist ${String(position)}` },
    swedebeat:
      status === 'missing' ? null : { title: `Hit ${String(position)}`, artist: `Band ${String(position)}`, duration: 241 },
    ...extra,
  }
}

function matchBody(songs = [song(1, 'found'), song(2, 'unsure'), song(3, 'missing')]) {
  const count = (s: string) => songs.filter((x) => x.status === s).length
  return {
    id: MATCH_ID,
    number: 3,
    kind: 'swedebeat_match',
    parent_id: LIST_ID,
    content_hash: HASH,
    songs,
    found: count('found'),
    unsure: count('unsure'),
    missing: count('missing'),
    swedebeat_sec: 700,
  }
}

function handoffBody(extra: Record<string, unknown> = {}) {
  return {
    state: 'open',
    verdict: null,
    mix_id: null,
    candidates: [],
    missing_positions: [],
    analysed_sec: null,
    expected_low_sec: null,
    expected_high_sec: null,
    expires_at: '2026-11-09T10:00:00Z',
    checked_at: null,
    ...extra,
  }
}

function routes(
  cls: Record<string, unknown> = {},
  more: Record<string, unknown> = {},
  swedebeat: 'connected' | 'needs_reconnect' | null = 'connected',
) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({
      cls: { songs: SONGS, playlist: PLAYLIST, swedebeat_match: { revision_id: MATCH_ID, approved: false }, ...cls },
    }),
    [`GET ${LIST_PATH}`]: listBody({ status: 'approved', total_sec: 660 }),
    [`GET ${MATCH_PATH}`]: matchBody(),
    'GET /api/connections': connections(swedebeat),
    ...more,
  } as Record<string, object>
}

beforeEach(() => {
  window.sessionStorage.clear()
  timing.pollMs = 1
})

/** Clicks a button once the page has finished loading and enabled it. */
async function press(name: string) {
  const button = await screen.findByRole('button', { name })
  await waitFor(() => {
    expect(button).toBeEnabled()
  })
  fireEvent.click(button)
}

function everyCallIsOurs(api: { calls: { path: string }[] }) {
  for (const call of api.calls) expect(call.path.startsWith('/api/')).toBe(true)
}

describe('the Swedebeat part of the class page', () => {
  it('shows nothing while the flag is off (no match key in the answer)', async () => {
    const api = fakeApi({ ...routes(), ...classRoutes({ cls: { songs: SONGS } }), [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }) })
    renderAt(PAGE)
    await screen.findByText('Tuesday climb')
    expect(screen.queryByText(/Swedebeat/)).toBeNull()
    expect(api.calls.some((c) => c.path.includes('swedebeat'))).toBe(false)
  })

  it('offers the match for an approved song list and starts the match job', async () => {
    const api = fakeApi({
      ...routes({ swedebeat_match: null }),
      'POST /api/jobs': () => json({ ...NEW_JOB, kind: 'swedebeat_match' }, 201),
    })
    renderAt(PAGE)
    const start = await screen.findByRole('button', { name: 'Match with Swedebeat' })
    await waitFor(() => {
      expect(start).toBeEnabled()
    })
    fireEvent.click(start)
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'swedebeat_match',
      input: { class_id: CLASS_ID, song_list_revision_id: LIST_ID, swedebeat_connection_id: SWEDE_ID },
    })
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    everyCallIsOurs(api)
  })

  it('asks to connect Swedebeat first when there is no connection', async () => {
    fakeApi(routes({ swedebeat_match: null }, {}, null))
    renderAt(PAGE)
    expect(await screen.findByRole('link', { name: /Connect Swedebeat on your account page/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Match with Swedebeat' })).toBeDisabled()
  })

  it('shows the reconnect prompt when the stored sign-in is refused', async () => {
    fakeApi(routes({ swedebeat_match: null }, {}, 'needs_reconnect'))
    renderAt(PAGE)
    const prompt = await screen.findByTestId('reconnect-prompt')
    expect(within(prompt).getByRole('link', { name: /Connect again/ })).toHaveAttribute('href', '/account#connections')
    expect(screen.getByRole('button', { name: 'Match with Swedebeat' })).toBeDisabled()
  })

  it('does not show the match button without an approved song list', async () => {
    fakeApi(routes({ swedebeat_match: null, songs: { ...SONGS, state: 'waiting' } }))
    renderAt(PAGE)
    await screen.findByText('Tuesday climb')
    expect(screen.queryByRole('button', { name: 'Match with Swedebeat' })).toBeNull()
  })

  it('maps a refused match start to a sentence', async () => {
    fakeApi({ ...routes({ swedebeat_match: null }), 'POST /api/jobs': () => reason(409, 'class_busy') })
    renderAt(PAGE)
    const start = await screen.findByRole('button', { name: 'Match with Swedebeat' })
    await waitFor(() => {
      expect(start).toBeEnabled()
    })
    fireEvent.click(start)
    expect(await screen.findByText(/already has a task running/)).toBeInTheDocument()
  })
})

describe('the match page', () => {
  it('shows found, unsure and missing songs as text with the totals and the length', async () => {
    const api = fakeApi(routes())
    renderAt(PAGE)
    const region = await screen.findByRole('region', { name: 'Swedebeat match' })
    expect(await within(region).findAllByTestId('match-song')).toHaveLength(3)
    expect(within(region).getByText('Found in Swedebeat')).toBeInTheDocument()
    expect(within(region).getByText('Unsure match')).toBeInTheDocument()
    expect(within(region).getByText('Not in Swedebeat')).toBeInTheDocument()
    expect(within(region).getByTestId('match-totals')).toHaveTextContent('1 found, 1 unsure, 1 not in Swedebeat.')
    await waitFor(() => {
      expect(within(region).getByTestId('match-length')).toHaveTextContent('11:40 long by Swedebeat')
      expect(within(region).getByTestId('match-length')).toHaveTextContent('against 11:00 planned')
    })
    // Server text is data: rendered as text.
    expect(within(region).getByText('Wanted 1 <i>x</i>')).toBeInTheDocument()
    expect(region.querySelector('i')).toBeNull()
    expect(within(region).getByText(/the mix will lack it/)).toBeInTheDocument()
    expect(within(region).getByText(/Check the unsure songs/)).toBeInTheDocument()
    expect(screen.queryByTestId('next-step')).toBeNull()
    everyCallIsOurs(api)
  })

  it('says nothing about missing songs when all are found', async () => {
    fakeApi(routes({}, { [`GET ${MATCH_PATH}`]: matchBody([song(1, 'found'), song(2, 'found')]) }))
    renderAt(PAGE)
    const region = await screen.findByRole('region', { name: 'Swedebeat match' })
    await within(region).findAllByTestId('match-song')
    expect(within(region).queryByText(/will lack/)).toBeNull()
    expect(within(region).queryByRole('button', { name: /Replace the missing/ })).toBeNull()
  })

  it('approves the selection with the revision id only, then reloads', async () => {
    let approved = false
    const api = fakeApi({
      ...routes(),
      [`GET ${CLASS_URL}`]: () => ({
        ...classBody({ songs: SONGS, playlist: PLAYLIST }),
        swedebeat_match: { revision_id: MATCH_ID, approved },
      }),
      [`POST ${CLASS_URL}/swedebeat-match`]: () => {
        approved = true
        return { revision_id: MATCH_ID, content_hash: HASH }
      },
      [`GET ${HANDOFF}`]: () => reason(404, 'not_found'),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve this selection' }))
    expect(await screen.findByText('Selection approved')).toBeInTheDocument()
    expect(api.to('POST', `${CLASS_URL}/swedebeat-match`)[0]?.body).toEqual({ revision_id: MATCH_ID })
    expect(await screen.findByRole('heading', { name: 'Next, in the Swedebeat app' })).toBeInTheDocument()
  })

  it('words a stale approval', async () => {
    fakeApi({ ...routes(), [`POST ${CLASS_URL}/swedebeat-match`]: () => reason(409, 'stale_match') })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve this selection' }))
    expect(await screen.findByText(/songs changed after this match was made/)).toBeInTheDocument()
  })

  it('replaces the missing and unsure songs by naming the match only', async () => {
    const api = fakeApi({
      ...routes(),
      'POST /api/jobs': () => json({ ...NEW_JOB, kind: 'revise_songs' }, 201),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace the missing and unsure songs' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    expect(api.to('POST', '/api/jobs')[0]?.body).toEqual({
      kind: 'revise_songs',
      input: {
        class_id: CLASS_ID,
        base_revision_id: REV1,
        match_revision_id: MATCH_ID,
        mode: 'pick',
        mowl_connection_id: MOWL_ID,
        spotify_connection_id: SPOTIFY_ID,
      },
    })
  })

  it('words the three-round limit', async () => {
    fakeApi({ ...routes(), 'POST /api/jobs': () => reason(409, 'swedebeat_rounds') })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace the missing and unsure songs' }))
    expect(await screen.findByText(/replaced three times already/)).toBeInTheDocument()
  })

  it('disables the decisions while a task runs', async () => {
    fakeApi(routes({ open_job: { id: NEW_JOB.id, kind: 'revise_songs', state: 'running' } }))
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Approve this selection' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Replace the missing and unsure songs' })).toBeDisabled()
  })

  it('shows a failure to load the match', async () => {
    fakeApi({ ...routes(), [`GET ${MATCH_PATH}`]: () => reason(500, 'internal') })
    renderAt(PAGE)
    expect(await screen.findByText(/match could not be loaded/)).toBeInTheDocument()
  })

  it('shows nothing when the flag turned off between the class and the match read', async () => {
    fakeApi({ ...routes(), [`GET ${MATCH_PATH}`]: () => reason(404, 'feature_off') })
    renderAt(PAGE)
    await screen.findByText('Tuesday climb')
    await waitFor(() => {
      expect(screen.queryByText(/Loading the Swedebeat match/)).toBeNull()
    })
    expect(screen.queryByRole('region', { name: 'Swedebeat match' })).toBeNull()
  })

  it('shows the reconnect prompt above an approved match when the connection needs it', async () => {
    fakeApi(routes({}, {}, 'needs_reconnect'))
    renderAt(PAGE)
    expect(await screen.findByTestId('reconnect-prompt')).toBeInTheDocument()
  })
})

describe('Next, in the Swedebeat app', () => {
  const APPROVED = { swedebeat_match: { revision_id: MATCH_ID, approved: true } }

  function step(more: Record<string, unknown> = {}, cls: Record<string, unknown> = {}) {
    return fakeApi({
      ...routes({ ...APPROVED, ...cls }),
      [`GET ${HANDOFF}`]: () => reason(404, 'not_found'),
      ...more,
    })
  }

  it('shows facts from the class and the two buttons', async () => {
    step()
    renderAt(PAGE)
    const region = await screen.findByRole('region', { name: 'Next, in the Swedebeat app' })
    expect(within(region).getByTestId('next-facts')).toHaveTextContent('Hills <b>mix</b>')
    // 700 s less 10 s for each of the 2 changes of song.
    expect(within(region).getByTestId('next-facts')).toHaveTextContent('3 songs, about 11:20 long')
    expect(within(region).getAllByRole('listitem')).toHaveLength(4)
    await waitFor(() => {
      expect(within(region).getByRole('button', { name: 'I made the mix' })).toBeEnabled()
    })
    expect(within(region).getByRole('button', { name: 'Not now' })).toBeEnabled()
  })

  it('starts the step on the first press, then checks and waits for the verify job', async () => {
    let checked = false
    const api = step({
      [`POST ${HANDOFF}`]: () => handoffBody(),
      [`POST ${HANDOFF}/check`]: () => json({ job_id: NEW_JOB.id, handoff: null }, 201),
      [`GET /api/jobs/${NEW_JOB.id}`]: () => {
        checked = true
        return jobBody('completed', { id: NEW_JOB.id, kind: 'swedebeat_verify_mix' })
      },
      [`GET ${HANDOFF}`]: () =>
        checked
          ? handoffBody({ state: 'verified', verdict: 'ok', mix_id: 'mix-1', checked_at: '2026-11-02T10:00:00Z' })
          : reason(404, 'not_found'),
    })
    renderAt(PAGE)
    await press('I made the mix')
    expect(await screen.findByText('Mix verified')).toBeInTheDocument()
    expect(screen.getByTestId('verdict')).toHaveTextContent('That is your mix')
    expect(api.to('POST', HANDOFF)).toHaveLength(1)
    const [check] = api.to('POST', `${HANDOFF}/check`)
    expect(check?.body).toEqual({})
    expect(check?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    expect(screen.queryByRole('button', { name: 'I made the mix' })).toBeNull()
    everyCallIsOurs(api)
  })

  it('says the mix is not there yet and then reads "Check again"', async () => {
    step({
      [`GET ${HANDOFF}`]: () =>
        handoffBody({ state: 'checked', verdict: 'not_found', checked_at: '2026-11-02T10:00:00Z' }),
      'GET /api/music/swedebeat-mixes': { mixes: [] },
    })
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeEnabled()
    expect(screen.getByTestId('verdict')).toHaveTextContent('has not shown a new mix yet')
    expect(screen.queryByRole('button', { name: 'I made the mix' })).toBeNull()
  })

  it('names the songs most likely missing when the mix is too short', async () => {
    step({
      [`GET ${HANDOFF}`]: () =>
        handoffBody({
          state: 'checked',
          verdict: 'too_short',
          missing_positions: [2, 3],
          mix_id: 'mix-1',
          candidates: ['mix-1'],
          checked_at: '2026-11-02T10:00:00Z',
        }),
      'GET /api/music/swedebeat-mixes': {
        mixes: [{ mix_id: 'mix-1', name: 'My mix', duration_seconds: 500, analysed: true }],
      },
    })
    renderAt(PAGE)
    expect(await screen.findByTestId('verdict')).toHaveTextContent('shorter than expected. Songs 2, 3 may be missing.')
    expect(screen.getByRole('button', { name: 'This is the right mix' })).toBeEnabled()
  })

  it('says a too long mix is too long', async () => {
    step({
      [`GET ${HANDOFF}`]: () =>
        handoffBody({ state: 'checked', verdict: 'too_long', checked_at: '2026-11-02T10:00:00Z' }),
      'GET /api/music/swedebeat-mixes': { mixes: [] },
    })
    renderAt(PAGE)
    expect(await screen.findByTestId('verdict')).toHaveTextContent('longer than expected')
  })

  it('lets the user pick which mix they made and sends only that id', async () => {
    const api = step({
      [`GET ${HANDOFF}`]: () =>
        handoffBody({
          state: 'checked',
          verdict: 'not_found',
          candidates: ['mix-2'],
          checked_at: '2026-11-02T10:00:00Z',
        }),
      'GET /api/music/swedebeat-mixes': {
        mixes: [
          { mix_id: 'mix-1', name: 'Old <b>mix</b>', duration_seconds: 900, analysed: true },
          { mix_id: 'mix-2', name: 'Fresh mix', duration_seconds: 700, analysed: false },
        ],
      },
      [`POST ${HANDOFF}/check`]: () => json({ job_id: null, handoff: handoffBody({ state: 'verified', verdict: 'ok', mix_id: 'mix-1' }) }),
    })
    renderAt(PAGE)
    const group = await screen.findByRole('group', { name: 'Which one did you make?' })
    const radios = await within(group).findAllByRole('radio')
    // The new mix is listed first.
    expect(radios[0]).toHaveAccessibleName(/Fresh mix/)
    expect(within(group).getByText('Old <b>mix</b> (15:00)')).toBeInTheDocument()
    fireEvent.click(within(group).getByRole('radio', { name: /Old/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    expect(await screen.findByText('Mix verified')).toBeInTheDocument()
    expect(api.to('POST', `${HANDOFF}/check`)[0]?.body).toEqual({ mix_id: 'mix-1' })
  })

  it('accepts the user saying it is the right mix', async () => {
    const api = step({
      [`GET ${HANDOFF}`]: () =>
        handoffBody({
          state: 'checked',
          verdict: 'too_short',
          mix_id: 'mix-1',
          candidates: ['mix-1'],
          checked_at: '2026-11-02T10:00:00Z',
        }),
      'GET /api/music/swedebeat-mixes': { mixes: [] },
      [`POST ${HANDOFF}/override`]: () => handoffBody({ state: 'verified', verdict: 'too_short', mix_id: 'mix-1' }),
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'This is the right mix' }))
    expect(await screen.findByText('Mix verified')).toBeInTheDocument()
    expect(api.to('POST', `${HANDOFF}/override`)[0]?.body).toEqual({ mix_id: 'mix-1' })
  })

  it('designs the class on a verified mix', async () => {
    const api = step({
      [`GET ${HANDOFF}`]: () => handoffBody({ state: 'verified', verdict: 'ok', mix_id: 'mix-1', checked_at: '2026-11-02T10:00:00Z' }),
      [`POST ${HANDOFF}/design`]: () => json({ job_id: NEW_JOB.id, revision_id: null, designed: false }, 201),
    })
    renderAt(PAGE)
    await press('Design the class on this mix')
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    expect(api.to('POST', `${HANDOFF}/design`)[0]?.body).toEqual({})
  })

  it('"Not now" writes nothing and can be taken back', async () => {
    const api = step({ [`POST ${HANDOFF}/not-now`]: () => ({ dismissed: true, handoff: null }) })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Not now' }))
    expect(await screen.findByText(/Your selection stays approved/)).toBeInTheDocument()
    expect(api.to('POST', `${HANDOFF}/not-now`)).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Show the steps again' }))
    expect(await screen.findByRole('button', { name: 'I made the mix' })).toBeInTheDocument()
  })

  it('offers a new step when the old one expired', async () => {
    const api = step({
      [`GET ${HANDOFF}`]: () => reason(410, 'handoff_expired'),
      [`POST ${HANDOFF}`]: () => handoffBody(),
      [`POST ${HANDOFF}/check`]: () => json({ job_id: null, handoff: handoffBody({ state: 'checked', verdict: 'not_found', checked_at: '2026-11-02T10:00:00Z' }) }),
      'GET /api/music/swedebeat-mixes': { mixes: [] },
    })
    renderAt(PAGE)
    expect(await screen.findByText(/This step has expired/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'I made the mix' }))
    expect(await screen.findByText(/has not shown a new mix yet/)).toBeInTheDocument()
    expect(api.to('POST', HANDOFF)).toHaveLength(1)
  })

  it('shows the reconnect prompt when a check is refused for the sign-in', async () => {
    step({
      [`POST ${HANDOFF}`]: () => handoffBody(),
      [`POST ${HANDOFF}/check`]: () => reason(409, 'needs_reconnect'),
    })
    renderAt(PAGE)
    await press('I made the mix')
    expect(await screen.findByTestId('reconnect-prompt')).toBeInTheDocument()
  })

  it('shows the reconnect prompt when the verify job failed for the sign-in', async () => {
    step({
      [`POST ${HANDOFF}`]: () => handoffBody(),
      [`POST ${HANDOFF}/check`]: () => json({ job_id: NEW_JOB.id, handoff: null }, 201),
      [`GET /api/jobs/${NEW_JOB.id}`]: () => jobBody('failed', { id: NEW_JOB.id, error_category: 'needs_reconnect' }),
    })
    renderAt(PAGE)
    await press('I made the mix')
    expect(await screen.findByTestId('reconnect-prompt')).toBeInTheDocument()
    expect(screen.getByText('MOWL no longer accepts the stored sign-in')).toBeInTheDocument()
  })

  it('words a busy class and the daily analysis limit', async () => {
    step({
      [`POST ${HANDOFF}`]: () => handoffBody(),
      [`POST ${HANDOFF}/check`]: () => reason(409, 'analysis_limit'),
    })
    renderAt(PAGE)
    await press('I made the mix')
    expect(await screen.findByText(/analysed the most mixes allowed for today/)).toBeInTheDocument()
  })

  it('is absent until the selection is approved, and never calls the hand-off before', async () => {
    const api = fakeApi(routes())
    renderAt(PAGE)
    await screen.findByRole('region', { name: 'Swedebeat match' })
    expect(screen.queryByRole('region', { name: 'Next, in the Swedebeat app' })).toBeNull()
    expect(api.calls.some((c) => c.path.includes('swedebeat-handoff'))).toBe(false)
  })

  it('has the structure an audit looks for: labelled regions, headings, named buttons and radios', async () => {
    step({
      [`GET ${HANDOFF}`]: () => handoffBody({ state: 'checked', verdict: 'not_found', checked_at: '2026-11-02T10:00:00Z' }),
      'GET /api/music/swedebeat-mixes': { mixes: [{ mix_id: 'm', name: 'M', duration_seconds: 60, analysed: true }] },
    })
    const { container } = renderAt(PAGE)
    await screen.findByRole('group', { name: 'Which one did you make?' })
    for (const region of screen.getAllByRole('region')) expect(region).toHaveAccessibleName()
    for (const control of container.querySelectorAll('button, input')) {
      const name = (control.getAttribute('aria-label') ?? control.textContent ?? '').trim()
      const labelled = (control as HTMLInputElement).labels?.length ?? 0
      expect(name !== '' || labelled > 0).toBe(true)
    }
    // Status is text, not colour alone.
    expect(container.querySelectorAll('[data-testid="verdict"]')).toHaveLength(1)
    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(|hsl\(/i)
  })
})
