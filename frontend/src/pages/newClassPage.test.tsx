import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { fakeApi, json, reason } from '../test/fakeApi'
import { JOB_ID, PLAYLIST_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import { renderAt } from '../test/render'

const CONNECTION_ID = 'c0000000-0000-4000-8000-000000000001'
const MOWL = {
  id: CONNECTION_ID,
  provider: 'mowl',
  status: 'connected',
  last_error: null,
  connected_at: '2026-10-28T10:00:00Z',
  last_used_at: null,
  expires_at: null,
}
const READY = {
  ...SESSION_ROUTES,
  'GET /api/connections': { connections: [MOWL] },
  'GET /api/music/mowl-playlists': {
    playlists: [
      { spotify_playlist_id: PLAYLIST_ID, name: 'Hills <script>x</script>' },
      { spotify_playlist_id: 'ZyXwVuTsRqPoNmLkJiHgFe', name: 'Sprints' },
    ],
  },
}
const CREATED = {
  'POST /api/jobs': () => json(jobBody('queued'), 201),
  [`GET /api/jobs/${JOB_ID}`]: jobBody('running'),
  [`GET /api/jobs/${JOB_ID}/events`]: openStream,
}

async function fill(name = 'Tuesday climb') {
  fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: name } })
  fireEvent.click(screen.getByRole('radio', { name: 'Hills <script>x</script>' }))
}

describe('the new-class form', () => {
  it('sends the brief, the playlist and the own connection id, with an idempotency key and CSRF', async () => {
    const api = fakeApi({ ...READY, ...CREATED })
    renderAt('/classes/new/advanced')
    await fill()
    fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '60' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Hard' }))
    fireEvent.change(screen.getByLabelText('Climbs'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('FTP test'), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText('Style'), { target: { value: 'hills' } })
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: 'No standing please' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/jobs/${JOB_ID}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'design_draft',
      input: {
        title: 'Tuesday climb',
        brief: {
          length_min: 60,
          intensity: 'hard',
          climbs: 2,
          sprints: 0,
          races: 0,
          ftp_test: 20,
          style: 'hills',
          text: 'No standing please',
          explicit: ['length_min', 'climbs', 'sprints', 'races', 'ftp_test'],
        },
        music: { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_ID },
        mowl_connection_id: CONNECTION_ID,
      },
    })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    expect(await screen.findByRole('heading', { name: 'Designing Tuesday climb…' })).toBeInTheDocument()
  })

  it('refuses a brief over 4096 bytes before sending, counting bytes and not characters', async () => {
    const api = fakeApi({ ...READY, ...CREATED })
    renderAt('/classes/new/advanced')
    await fill()
    // 2000 characters (the field's limit), but 4000 bytes of text plus the rest of the brief: over the limit.
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: 'å'.repeat(2000) } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByText(/together are too long \(4096 bytes at most\)/)).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
    // The same number of characters in plain letters is 2000 bytes and goes through.
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: 'a'.repeat(2000) } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
  })

  it('says the playlist is added to the MOWL music, and shows playlist names as text', async () => {
    fakeApi(READY)
    renderAt('/classes/new/advanced')
    await fill()
    expect(screen.getByText(/Added to your MOWL music, private to you/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Hills <script>x</script>' })).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
  })

  it('has one shared radio group per choice and the create button in the sticky bar, with no inline styles', async () => {
    fakeApi(READY)
    renderAt('/classes/new/advanced')
    await screen.findByLabelText('Class name')
    const form = screen.getByRole('form', { name: 'New class' })
    const groups = within(form).getAllByRole('radiogroup')
    expect(groups.length).toBeGreaterThanOrEqual(2)
    for (const name of ['Length', 'Effort']) {
      expect(within(form).getByRole('radiogroup', { name })).toBeInTheDocument()
    }
    const bar = within(form).getByRole('button', { name: 'Design my class' }).closest('section')
    expect(bar).toHaveAttribute('aria-label')
    expect(bar).toHaveClass('lg:sticky', 'max-lg:fixed')
    expect(form.querySelector('[style]')).toBeNull()
  })

  it('keeps one idempotency key for the same form and a new one when it changes', async () => {
    let answers = 0
    const api = fakeApi({
      ...READY,
      'POST /api/jobs': () => {
        answers += 1
        return answers <= 2 ? reason(502, 'bad_gateway') : json(jobBody('queued'), 201)
      },
      [`GET /api/jobs/${JOB_ID}`]: jobBody('running'),
      [`GET /api/jobs/${JOB_ID}/events`]: openStream,
    })
    renderAt('/classes/new/advanced')
    await fill()
    const submit = screen.getByRole('button', { name: 'Design my class' })
    fireEvent.click(submit)
    await screen.findByRole('alert')
    fireEvent.click(submit)
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(2)
    })
    fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '50' } })
    fireEvent.click(submit)
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(3)
    })
    const keys = api.to('POST', '/api/jobs').map((c) => c.headers.get('Idempotency-Key'))
    expect(keys[0]).toBe(keys[1])
    expect(keys[2]).not.toBe(keys[0])
  })

  it('checks the fields before sending anything', async () => {
    const api = fakeApi(READY)
    renderAt('/classes/new/advanced')
    await screen.findByLabelText('Class name')
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Give the class a name')
    await fill()
    fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('10 to 180')
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it.each([
    ['class_busy', 409, 'already has a design running'],
    ['not_connected', 409, 'MOWL is not connected'],
    ['running_not_evaluated', 409, 'Running classes are not available on your plan yet. Nothing was charged.'],
    ['busy', 429, 'already have designs running'],
  ])('explains the refusal %s in words', async (why, status, text) => {
    fakeApi({ ...READY, 'POST /api/jobs': reason(status, why) })
    renderAt('/classes/new/advanced')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(document.body).not.toHaveTextContent(why)
  })

  it('words a quota refusal with the numbers and the period end', async () => {
    fakeApi({
      ...READY,
      'POST /api/jobs': () =>
        json(
          { detail: { reason: 'quota', quota: 'classes', limit: 3, used: 3, period_end: '2026-11-01T00:00:00Z', plan: 'free' } },
          409,
        ),
    })
    renderAt('/classes/new/advanced')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You have used your 3 classes for October. More from 1 November.',
    )
  })

  it('words busy with the limit of the plan', async () => {
    fakeApi({ ...READY, 'POST /api/jobs': () => json({ detail: { reason: 'busy', limit: 2 } }, 429) })
    renderAt('/classes/new/advanced')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('allows 2 designs at a time')
  })

  it('asks the user to connect MOWL first', async () => {
    fakeApi({ ...SESSION_ROUTES })
    renderAt('/classes/new/advanced')
    expect(await screen.findByRole('link', { name: 'Connect MOWL' })).toHaveAttribute('href', '/en/account')
    expect(screen.queryByLabelText('Class name')).toBeNull()
  })

  it('asks to connect MOWL again when its sign-in no longer works', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      'GET /api/connections': { connections: [{ ...MOWL, status: 'needs_reconnect' }] },
    })
    renderAt('/classes/new/advanced')
    expect(await screen.findByRole('link', { name: 'Connect MOWL again' })).toBeInTheDocument()
  })

  it('offers a retry when the playlists cannot be loaded, without a provider text', async () => {
    let tries = 0
    fakeApi({
      ...READY,
      'GET /api/music/mowl-playlists': () => {
        tries += 1
        return tries === 1 ? reason(503, 'upstream_unavailable') : READY['GET /api/music/mowl-playlists']
      },
    })
    renderAt('/classes/new/advanced')
    expect(await screen.findByRole('alert')).toHaveTextContent('MOWL could not be reached')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByLabelText('Class name')).toBeInTheDocument()
  })

  it('says so when MOWL lists no playlists', async () => {
    fakeApi({ ...READY, 'GET /api/music/mowl-playlists': { playlists: [] } })
    renderAt('/classes/new/advanced')
    expect(await screen.findByRole('heading', { name: 'No playlists found' })).toBeInTheDocument()
  })

  it('has segmented controls, one per choice, and steppers that stop at their limits', async () => {
    fakeApi(READY)
    renderAt('/classes/new/advanced?length=60&climbs=10')
    await screen.findByLabelText('Class name')
    expect(screen.getByRole('radio', { name: '60 min' })).toBeChecked()
    expect(screen.getByRole('radio', { name: '30 min' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'More climbs' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Fewer climbs' }))
    expect(screen.getByLabelText('Climbs')).toHaveValue(9)
    fireEvent.click(screen.getByRole('button', { name: 'More sprints' }))
    expect(screen.getByLabelText('Sprints')).toHaveValue(1)
    expect(screen.getByRole('button', { name: 'Fewer sprints' })).not.toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Pop' }))
    expect(screen.getByLabelText('Style')).toHaveValue('Pop')
    fireEvent.click(screen.getByRole('button', { name: '90s' }))
    expect(screen.getByLabelText('Style')).toHaveValue('Pop, 90s')
    expect(screen.getByRole('button', { name: 'Pop' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '90s' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Rock' })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: 'Pop' }))
    expect(screen.getByRole('button', { name: 'Pop' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByLabelText('Style')).toHaveValue('90s')
    expect(document.body).not.toHaveTextContent(/zones? \d/i)
  })

  // Every length option and every style chip writes its value (English, whatever the label) into the brief.
  const LENGTH_CHIPS = [
    ['30 min', 30],
    ['45 min', 45],
    ['60 min', 60],
    ['90 min', 90],
  ] as const
  it.each(LENGTH_CHIPS)('sends the brief of the %s option', async (label, minutes) => {
    const api = fakeApi({ ...READY, ...CREATED })
    renderAt('/classes/new/advanced')
    await fill()
    fireEvent.click(screen.getByRole('radio', { name: label }))
    expect(screen.queryByLabelText('Length in minutes')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const body = api.to('POST', '/api/jobs')[0]?.body as { input: { brief: { length_min: number } } }
    expect(body.input.brief.length_min).toBe(minutes)
  })

  const STYLE_CHIPS = [
    ['80s', '80s'],
    ['90s', '90s'],
    ['2000s', '2000s'],
    ['Pop', 'Pop'],
    ['Rock', 'Rock'],
    ['Dance', 'Dance'],
    ['Hip hop', 'Hip hop'],
    ['Latin', 'Latin'],
  ] as const
  it.each(STYLE_CHIPS)('sends the style of the %s chip', async (label, value) => {
    const api = fakeApi({ ...READY, ...CREATED })
    renderAt('/classes/new/advanced')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: label }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const body = api.to('POST', '/api/jobs')[0]?.body as { input: { brief: { style: string } } }
    expect(body.input.brief.style).toBe(value)
  })

  it('lists the playlists as radio tiles in one group and prefills from a preset', async () => {
    fakeApi(READY)
    renderAt('/classes/new/advanced?intensity=hard')
    await screen.findByLabelText('Class name')
    const group = screen.getByRole('radiogroup', { name: 'Playlist' })
    expect(group.querySelectorAll('input[type="radio"]')).toHaveLength(2)
    expect(screen.getByRole('radio', { name: 'Hard' })).toBeChecked()
    expect(screen.getByRole('region', { name: 'Your class so far' })).toBeInTheDocument()
  })

  it('shows the classes left this month when the plan is known, and nothing otherwise', async () => {
    fakeApi({
      ...READY,
      'GET /api/account/plan': {
        plan: 'free', name: 'Free', source: null, ends_at: null,
        limits: { classes_per_month: 3, revisions_per_class: 5, concurrent_jobs: 1, max_class_minutes: 60 },
        gates: { games: true, races: true, ftp_tests: true },
        classes_held: 1, period_end: '2026-11-01T00:00:00Z', cost_share: 0, features: [], trial: null,
      },
    })
    renderAt('/classes/new/advanced')
    expect(await screen.findByText('Uses 1 of your 2 classes left this month')).toBeInTheDocument()
  })

  describe('the plan limits (#496)', () => {
    const plan = (maxMinutes: number, gates: { games: boolean; races: boolean; ftp_tests: boolean }) => ({
      plan: 'free', name: 'Free', source: null, ends_at: null,
      limits: { classes_per_month: 3, revisions_per_class: 5, concurrent_jobs: 1, max_class_minutes: maxMinutes },
      gates,
      classes_held: 0, period_end: '2026-11-01T00:00:00Z', cost_share: 0, features: [], trial: null,
    })

    it('disables lengths over the cap and says what the plan allows', async () => {
      fakeApi({ ...READY, 'GET /api/account/plan': plan(45, { games: true, races: true, ftp_tests: true }) })
      renderAt('/classes/new/advanced')
      expect(await screen.findByText('Your plan allows classes of up to 45 minutes.')).toBeInTheDocument()
      expect(screen.getByRole('radio', { name: '45 min' })).toBeEnabled()
      expect(screen.getByRole('radio', { name: '60 min' })).toBeDisabled()
      expect(screen.getByRole('radio', { name: '90 min' })).toBeDisabled()
      fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
      expect(screen.getByLabelText('Length in minutes')).toHaveAttribute('max', '45')
    })

    it('does not send a typed length over the cap', async () => {
      const api = fakeApi({
        ...READY,
        ...CREATED,
        'GET /api/account/plan': plan(45, { games: true, races: true, ftp_tests: true }),
      })
      renderAt('/classes/new/advanced')
      await screen.findByText('Your plan allows classes of up to 45 minutes.')
      await fill()
      fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
      fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '60' } })
      fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
      expect(await screen.findAllByText('Your plan allows classes of up to 45 minutes.')).toHaveLength(2)
      expect(api.to('POST', '/api/jobs')).toHaveLength(0)
    })

    it('hides races and the FTP test when the plan leaves them out, and sends none', async () => {
      const api = fakeApi({
        ...READY,
        ...CREATED,
        'GET /api/account/plan': plan(60, { games: false, races: false, ftp_tests: false }),
      })
      renderAt('/classes/new/advanced?races=3')
      expect(await screen.findByText('Your plan does not include races and FTP tests.')).toBeInTheDocument()
      expect(screen.queryByLabelText('Races')).toBeNull()
      expect(screen.queryByLabelText('FTP test')).toBeNull()
      expect(screen.getByText(/Games are not in your plan\./)).toBeInTheDocument()
      await fill()
      fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
      await waitFor(() => {
        expect(api.to('POST', '/api/jobs')).toHaveLength(1)
      })
      const brief = (api.to('POST', '/api/jobs')[0]?.body as { input: { brief: Record<string, unknown> } }).input.brief
      expect(brief.races).toBe(0)
      expect(brief.ftp_test).toBeNull()
    })

    it('shows everything and no sentence about games when the plan includes them', async () => {
      fakeApi({ ...READY, 'GET /api/account/plan': plan(180, { games: true, races: true, ftp_tests: true }) })
      renderAt('/classes/new/advanced')
      await screen.findByText('Uses 1 of your 3 classes left this month')
      expect(screen.queryByText(/Your plan allows classes/)).toBeNull()
      expect(screen.getByLabelText('Races')).toBeInTheDocument()
      expect(screen.getByLabelText('FTP test')).toBeInTheDocument()
      expect(screen.queryByText(/Games are not part/)).toBeNull()
      expect(screen.queryByText(/does not include/)).toBeNull()
    })

    const NONE = { games: false, races: false, ftp_tests: false }
    const RUNNING = { ...READY, 'GET /api/connections': { connections: [MOWL], running_enabled: true } }

    it('caps a running class by the plan and sends the running body with no bike fields', async () => {
      const api = fakeApi({ ...RUNNING, ...CREATED, 'GET /api/account/plan': plan(45, NONE) })
      renderAt('/classes/new/advanced')
      await screen.findByText('Your plan allows classes of up to 45 minutes.')
      fireEvent.click(screen.getByRole('radio', { name: 'Running' }))
      // The races / FTP sentence and the controls belong to cycling only.
      expect(screen.queryByText(/does not include/)).toBeNull()
      expect(screen.queryByLabelText('Races')).toBeNull()
      expect(screen.queryByLabelText('FTP test')).toBeNull()
      const summary = within(screen.getByRole('region', { name: 'Your class so far' }))
      expect(summary.getByText('Sport')).toBeInTheDocument()
      expect(summary.getByText('Running')).toBeInTheDocument()
      expect(screen.getByRole('radio', { name: '60 min' })).toBeDisabled()
      await fill()
      fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
      fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '60' } })
      fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
      expect(await screen.findAllByText('Your plan allows classes of up to 45 minutes.')).toHaveLength(2)
      expect(api.to('POST', '/api/jobs')).toHaveLength(0)
      fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
      fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '40' } })
      fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
      await waitFor(() => {
        expect(api.to('POST', '/api/jobs')).toHaveLength(1)
      })
      const brief = (api.to('POST', '/api/jobs')[0]?.body as { input: { brief: Record<string, unknown> } }).input.brief
      expect(Object.keys(brief).sort()).toEqual(['explicit', 'intensity', 'length_min', 'sport', 'style', 'text'])
      expect(brief.sport).toBe('running')
      expect(brief.length_min).toBe(40)
    })

    it('keeps the cycling brief on the same plan: no races, no FTP test, the sentence shown', async () => {
      const api = fakeApi({ ...RUNNING, ...CREATED, 'GET /api/account/plan': plan(45, NONE) })
      renderAt('/classes/new/advanced')
      await screen.findByText('Your plan allows classes of up to 45 minutes.')
      expect(screen.getByRole('radio', { name: 'Cycling' })).toBeChecked()
      expect(screen.getByText('Your plan does not include races and FTP tests.')).toBeInTheDocument()
      expect(screen.getByLabelText('Climbs')).toBeInTheDocument()
      await fill()
      fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
      fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '45' } })
      fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
      await waitFor(() => {
        expect(api.to('POST', '/api/jobs')).toHaveLength(1)
      })
      const brief = (api.to('POST', '/api/jobs')[0]?.body as { input: { brief: Record<string, unknown> } }).input.brief
      expect(brief).toMatchObject({ length_min: 45, races: 0, ftp_test: null })
      expect(brief.sport).toBeUndefined()
    })

    it('sends no FTP test when it was picked before the plan answered and the gate turns out off', async () => {
      const api = fakeApi({ ...READY, ...CREATED, 'GET /api/account/plan': plan(60, NONE) })
      let release: () => void = () => undefined
      const held = new Promise<void>((resolve) => {
        release = resolve
      })
      const real = globalThis.fetch
      vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
        const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
        return path === '/api/account/plan' ? held.then(() => real(input, init)) : real(input, init)
      })
      renderAt('/classes/new/advanced')
      await fill()
      fireEvent.change(screen.getByLabelText('FTP test'), { target: { value: '20' } })
      release()
      await screen.findByText('Your plan does not include races and FTP tests.')
      expect(screen.queryByLabelText('FTP test')).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
      await waitFor(() => {
        expect(api.to('POST', '/api/jobs')).toHaveLength(1)
      })
      const brief = (api.to('POST', '/api/jobs')[0]?.body as { input: { brief: Record<string, unknown> } }).input.brief
      expect(brief.ftp_test).toBeNull()
    })

    it('never renders or asks for a price', async () => {
      const api = fakeApi({
        ...READY,
        'GET /api/account/plan': { ...plan(30, { games: false, races: true, ftp_tests: true }), monthly_price_cents: 990, currency: 'USD' },
      })
      const { container } = renderAt('/classes/new/advanced')
      await screen.findByText('Your plan allows classes of up to 30 minutes.')
      expect(container.textContent).not.toMatch(/price|\$|USD|990|9\.90/i)
      expect(api.calls.every((c) => !c.path.startsWith('/api/admin'))).toBe(true)
    })
  })

  it('hides the classes line when the plan cannot be read', async () => {
    // The default fake serves a plan; this test fails that read on purpose.
    const api = fakeApi({ ...READY, 'GET /api/account/plan': () => reason(503, 'upstream_unavailable') })
    renderAt('/classes/new/advanced')
    await screen.findByLabelText('Class name')
    // The refusal is answered once the request is recorded; nothing can show the line after that.
    await waitFor(() => {
      expect(api.to('GET', '/api/account/plan')).toHaveLength(1)
    })
    await Promise.resolve()
    expect(screen.queryByText(/classes left this month/)).toBeNull()
  })

  it('is for signed-in users only', async () => {
    fakeApi({})
    renderAt('/classes/new/advanced')
    expect(await screen.findByRole('heading', { name: 'Sign in first' })).toBeInTheDocument()
  })

  it('does not call the mix list or show a third music choice when Swedebeat is off', async () => {
    const api = fakeApi({
      ...READY,
      'GET /api/connections': {
        connections: [MOWL],
        swedebeat: { enabled: false, available: false },
      },
    })
    renderAt('/classes/new/advanced')
    await screen.findByLabelText('Class name')
    expect(screen.queryByRole('radio', { name: 'Swedebeat mix' })).toBeNull()
    expect(api.to('GET', '/api/music/swedebeat-mixes')).toHaveLength(0)
  })

  it('does not call the mix list or show the mix choice while "I use Swedebeat" is off (#525)', async () => {
    const api = fakeApi({
      ...READY,
      'GET /api/connections': {
        connections: [MOWL],
        swedebeat: { enabled: true, available: true, uses_swedebeat: false },
      },
    })
    renderAt('/classes/new/advanced')
    await screen.findByLabelText('Class name')
    expect(screen.queryByRole('radio', { name: 'Swedebeat mix' })).toBeNull()
    expect(api.to('GET', '/api/music/swedebeat-mixes')).toHaveLength(0)
  })

  it('designs on a chosen Swedebeat mix with plain list errors', async () => {
    const mixId = 'mix-sunday-climb'
    const api = fakeApi({
      ...SESSION_ROUTES,
      'GET /api/connections': {
        connections: [MOWL],
        swedebeat: { enabled: true, available: true, uses_swedebeat: true },
      },
      'GET /api/music/mowl-playlists': READY['GET /api/music/mowl-playlists'],
      'GET /api/music/swedebeat-mixes': {
        mixes: [{ mix_id: mixId, name: 'Sunday climb', duration_seconds: 1830, analysed: true }],
      },
      ...CREATED,
    })
    renderAt('/classes/new/advanced')
    await fill('Mix class')
    fireEvent.click(await screen.findByRole('radio', { name: 'Swedebeat mix' }))
    fireEvent.click(screen.getByRole('radio', { name: /Sunday climb/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/jobs/${JOB_ID}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toMatchObject({
      kind: 'design_draft',
      input: {
        title: 'Mix class',
        music: { source: 'mowl_swedebeat_mix', mix_id: mixId },
        mowl_connection_id: CONNECTION_ID,
      },
    })
  })

  it('words a failed mix listing without a reason code', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      'GET /api/connections': {
        connections: [MOWL],
        swedebeat: { enabled: true, available: true, uses_swedebeat: true },
      },
      'GET /api/music/mowl-playlists': READY['GET /api/music/mowl-playlists'],
      'GET /api/music/swedebeat-mixes': () => reason(503, 'upstream_unavailable'),
    })
    renderAt('/classes/new/advanced')
    await screen.findByLabelText('Class name')
    expect(screen.getByText('MOWL could not be reached. Try again in a few minutes.')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('upstream_unavailable')
    expect(screen.getByRole('radio', { name: 'Swedebeat mix' })).toBeDisabled()
  })

  it('shows a failed mix list when MOWL lists no Spotify playlists', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      'GET /api/connections': {
        connections: [MOWL],
        swedebeat: { enabled: true, available: true, uses_swedebeat: true },
      },
      'GET /api/music/mowl-playlists': { playlists: [] },
      'GET /api/music/swedebeat-mixes': () => reason(503, 'upstream_unavailable'),
    })
    renderAt('/classes/new/advanced')
    await screen.findByLabelText('Class name')
    expect(screen.getByText('MOWL could not be reached. Try again in a few minutes.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No playlists found' })).toBeNull()
    expect(document.body).not.toHaveTextContent('MOWL lists no Spotify playlists')
    expect(screen.getByRole('radio', { name: 'Swedebeat mix' })).toBeDisabled()
  })
})
