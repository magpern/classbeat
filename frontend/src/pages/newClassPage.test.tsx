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
  fireEvent.change(screen.getByLabelText('Playlist'), { target: { value: PLAYLIST_ID } })
}

describe('the new-class form', () => {
  it('sends the brief, the playlist and the own connection id, with an idempotency key and CSRF', async () => {
    const api = fakeApi({ ...READY, ...CREATED })
    renderAt('/classes/new')
    await fill()
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '60' } })
    fireEvent.change(screen.getByLabelText('Intensity'), { target: { value: 'hard' } })
    fireEvent.change(screen.getByLabelText('Climbs'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('FTP test'), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText('Style'), { target: { value: 'hills' } })
    fireEvent.change(screen.getByLabelText('Anything else'), { target: { value: 'No standing please' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design the class' }))
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

  it('says classbeat adds the playlist to the MOWL music, and shows playlist names as text', async () => {
    fakeApi(READY)
    renderAt('/classes/new')
    await fill()
    expect(screen.getByText(/classbeat adds this playlist to your MOWL music/)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Hills <script>x</script>' })).toBeInTheDocument()
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
    const submit = screen.getByRole('button', { name: 'Design the class' })
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
    fireEvent.click(screen.getByRole('button', { name: 'Design the class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Give the class a name')
    await fill()
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design the class' }))
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
    fireEvent.click(screen.getByRole('button', { name: 'Design the class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(document.body).not.toHaveTextContent(why)
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

  it('is for signed-in users only', async () => {
    fakeApi({})
    renderAt('/classes/new')
    expect(await screen.findByRole('heading', { name: 'Sign in first' })).toBeInTheDocument()
  })
})
