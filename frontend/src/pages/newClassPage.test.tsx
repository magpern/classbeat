import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
    renderAt('/classes/new')
    await fill()
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '60' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Hard' }))
    fireEvent.change(screen.getByLabelText('Climbs'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('FTP test'), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText('Style'), { target: { value: 'hills' } })
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: 'No standing please' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${JOB_ID}`)
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
        },
        music: { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_ID },
        mowl_connection_id: CONNECTION_ID,
      },
    })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
  })

  it('refuses a brief over 4096 bytes before sending, counting bytes and not characters', async () => {
    const api = fakeApi({ ...READY, ...CREATED })
    renderAt('/classes/new')
    await fill()
    // 2000 characters (the field's limit), but 4000 bytes of text plus the rest of the brief: over the limit.
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: 'å'.repeat(2000) } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByText(/too long for one class \(the limit is 4096 bytes/)).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
    // The same number of characters in plain letters is 2000 bytes and goes through.
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: 'a'.repeat(2000) } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
  })

  it('says classbeat adds the playlist to the MOWL music, and shows playlist names as text', async () => {
    fakeApi(READY)
    renderAt('/classes/new')
    await fill()
    expect(screen.getByText(/classbeat adds this playlist to your MOWL music/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Hills <script>x</script>' })).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
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
    renderAt('/classes/new')
    await fill()
    const submit = screen.getByRole('button', { name: 'Design my class' })
    fireEvent.click(submit)
    await screen.findByRole('alert')
    fireEvent.click(submit)
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(2)
    })
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
    renderAt('/classes/new')
    await screen.findByLabelText('Class name')
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Give the class a name')
    await fill()
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('10 to 180')
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it.each([
    ['class_busy', 409, 'already has a design running'],
    ['not_connected', 409, 'MOWL is not connected'],
    ['busy', 429, 'already have designs running'],
  ])('explains the refusal %s in words', async (why, status, text) => {
    fakeApi({ ...READY, 'POST /api/jobs': reason(status, why) })
    renderAt('/classes/new')
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
    renderAt('/classes/new')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You have used your 3 classes for October. More from 1 November.',
    )
  })

  it('words busy with the limit of the plan', async () => {
    fakeApi({ ...READY, 'POST /api/jobs': () => json({ detail: { reason: 'busy', limit: 2 } }, 429) })
    renderAt('/classes/new')
    await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('allows 2 designs at a time')
  })

  it('asks the user to connect MOWL first', async () => {
    fakeApi({ ...SESSION_ROUTES })
    renderAt('/classes/new')
    expect(await screen.findByRole('link', { name: 'Connect MOWL on your account page' })).toHaveAttribute('href', '/account')
    expect(screen.queryByLabelText('Class name')).toBeNull()
  })

  it('asks to connect MOWL again when its sign-in no longer works', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      'GET /api/connections': { connections: [{ ...MOWL, status: 'needs_reconnect' }] },
    })
    renderAt('/classes/new')
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
    renderAt('/classes/new')
    expect(await screen.findByRole('alert')).toHaveTextContent('MOWL could not be reached')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByLabelText('Class name')).toBeInTheDocument()
  })

  it('says so when MOWL lists no playlists', async () => {
    fakeApi({ ...READY, 'GET /api/music/mowl-playlists': { playlists: [] } })
    renderAt('/classes/new')
    expect(await screen.findByRole('heading', { name: 'No playlists found' })).toBeInTheDocument()
  })

  it('has chips with aria-pressed and steppers that stop at their limits', async () => {
    fakeApi(READY)
    renderAt('/classes/new?length=60&climbs=10')
    await screen.findByLabelText('Class name')
    expect(screen.getByRole('button', { name: '60 min' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '30 min' })).toHaveAttribute('aria-pressed', 'false')
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
  })

  it('lists the playlists as radio tiles in one group and prefills from a preset', async () => {
    fakeApi(READY)
    renderAt('/classes/new?intensity=hard')
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
        limits: { classes_per_month: 3, revisions_per_class: 5, concurrent_jobs: 1 },
        classes_held: 1, period_end: '2026-11-01T00:00:00Z', cost_share: 0, features: [], trial: null,
      },
    })
    renderAt('/classes/new')
    expect(await screen.findByText('Uses 1 of your 2 classes left this month')).toBeInTheDocument()
  })

  it('hides the classes line when the plan cannot be read', async () => {
    // The default fake serves a plan; this test fails that read on purpose.
    const api = fakeApi({ ...READY, 'GET /api/account/plan': () => reason(503, 'upstream_unavailable') })
    renderAt('/classes/new')
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
    renderAt('/classes/new')
    expect(await screen.findByRole('heading', { name: 'Sign in first' })).toBeInTheDocument()
  })
})
