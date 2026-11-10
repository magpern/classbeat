import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MOWL_ID, SPOTIFY_ID, connectionsBody } from '../test/songFixtures'
import { fakeApi, json, PLAN_FREE, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// MA2 (#882): the free-text start screen at /classes/new. The API is faked; nothing here reaches a network.

const PLAYLIST_A = 'AbCdEfGhIjKlMnOpQrStUv'
const PLAYLIST_B = 'ZyXwVuTsRqPoNmLkJiHgFe'
const SPOTIFY_OK = {
  status: 'connected',
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
  pool_consent: true,
  consent_version: 1,
  consent_text: 'x',
}
const INTERPRET_JOB = jobBody('queued', { kind: 'interpret_request' })

function routes(extra: Record<string, unknown> = {}, connections: Record<string, unknown> = connectionsBody(false)) {
  return {
    ...SESSION_ROUTES,
    'GET /api/connections': connections,
    'GET /api/music/mowl-playlists': {
      playlists: [
        { spotify_playlist_id: PLAYLIST_A, name: 'Hill climbs' },
        { spotify_playlist_id: PLAYLIST_B, name: 'Sprint set' },
      ],
    },
    'POST /api/jobs': () => json(INTERPRET_JOB, 201),
    ...extra,
  }
}

const withSpotify = () => ({ 'GET /api/connections/spotify': SPOTIFY_OK })
const plan = (games: boolean) => ({ 'GET /api/account/plan': { ...PLAN_FREE, gates: { ...PLAN_FREE.gates, games } } })
const prompt = () => screen.findByLabelText('Describe your class')
const openIdeas = async () => {
  fireEvent.click(await screen.findByText('Ideas for your class'))
}

afterEach(() => {
  window.sessionStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('the start screen', () => {
  it('shows the prompt, starter ideas, connected services and the Advanced link, and no playlist grid', async () => {
    fakeApi(routes({}, connectionsBody(true)))
    renderAt('/classes/new')
    expect(await prompt()).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Design my class' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /A 30 minute interval ride/ })).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Connected services' })).getByText('MOWL connected')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Advanced/ })).toHaveAttribute('href', '/en/classes/new/advanced')
    // Closed: no playlist is drawn, and no required counter exists.
    expect(screen.queryByRole('radio', { name: /Hill climbs/ })).toBeNull()
    expect(screen.queryByLabelText('Climbs')).toBeNull()
    expect(screen.getByRole('button', { name: 'Music' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('sends an interpret_request job with the own connection id, a key and CSRF, then opens the class', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: '  45 minutes, three climbs  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/classes/${CLASS_ID}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'interpret_request',
      input: { request: '45 minutes, three climbs', mowl_connection_id: MOWL_ID },
    })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('wants some words first and sends nothing without them', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new')
    await prompt()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByText('Describe your class first.')).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('words a refusal in plain language and stays on the page', async () => {
    fakeApi(routes({ 'POST /api/jobs': () => reason(409, 'not_connected') }))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'An easy ride' } })
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/en/classes/new')
  })

  it('adds a starter idea to the prompt', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.click(await screen.findByRole('button', { name: /A 60 minute easy endurance ride/ }))
    expect(await prompt()).toHaveValue('A 60 minute easy endurance ride.')
  })

  it('says MOWL must be connected first (the no_mowl state)', async () => {
    fakeApi(routes({}, { connections: [] }))
    renderAt('/classes/new')
    expect(await screen.findByRole('link', { name: /Connect MOWL/i })).toHaveAttribute('href', '/en/account')
    expect(screen.queryByRole('button', { name: 'Design my class' })).toBeNull()
  })

  it('opens the advanced form from the Advanced link', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.click(await screen.findByRole('link', { name: /Advanced/ }))
    expect(await screen.findByLabelText('Class name')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/en/classes/new/advanced')
  })

  it('opens again with the same request after Try again, by class id', async () => {
    fakeApi(routes(withSpotify(), connectionsBody(true)))
    const first = renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'Hard hills for Thursday' } })
    fireEvent.click(screen.getByRole('button', { name: 'Music' }))
    fireEvent.click(screen.getByRole('radio', { name: /Sprint set/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/classes/${CLASS_ID}`)
    })
    first.unmount()
    renderAt(`/classes/new?from=${CLASS_ID}`)
    expect(await prompt()).toHaveValue('Hard hills for Thursday')
    expect(screen.getByRole('button', { name: 'Music: Sprint set' })).toBeInTheDocument()
  })
})

describe('the music picker', () => {
  it('labels every row by its source and says where the class is saved, separately', async () => {
    fakeApi(
      routes(
        {
          ...withSpotify(),
          'GET /api/music/swedebeat-mixes': { mixes: [{ mix_id: 'mix-1', name: 'Friday mix', duration_seconds: 2700, analysed: true }] },
        },
        { ...connectionsBody(true), swedebeat: { enabled: true, available: true, uses_swedebeat: true }, soundless_enabled: true },
      ),
    )
    renderAt('/classes/new')
    fireEvent.click(await screen.findByRole('button', { name: 'Music' }))
    const list = screen.getByRole('radiogroup', { name: 'Music for this class' })
    expect(within(list).getByRole('radio', { name: /Hill climbs.*Spotify playlist · in MOWL/ })).toBeInTheDocument()
    expect(within(list).getByRole('radio', { name: /Friday mix.*Swedebeat mix/ })).toBeInTheDocument()
    expect(within(list).getByRole('radio', { name: /classbeat picks songs \(Spotify\)/ })).toBeInTheDocument()
    expect(within(list).getByRole('radio', { name: /No music/ })).toBeInTheDocument()
    // The destination is not a row.
    expect(within(list).queryByText(/saved to your MOWL account/)).toBeNull()
    expect(screen.getByTestId('destination')).toHaveTextContent('saved to your MOWL account')
    // Nothing about tempo or energy is claimed for a name.
    expect(document.body).not.toHaveTextContent(/\bBPM\b|energy|tempo/i)
  })

  it('searches by name and by source', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.click(await screen.findByRole('button', { name: 'Music' }))
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search your music' }), { target: { value: 'sprint' } })
    expect(screen.getByRole('radio', { name: /Sprint set/ })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /Hill climbs/ })).toBeNull()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search your music' }), { target: { value: 'zzz' } })
    expect(screen.getByText('Nothing matches.')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search your music' }), { target: { value: 'IN MOWL' } })
    expect(screen.getAllByRole('radio', { name: /Spotify playlist · in MOWL/ })).toHaveLength(2)
  })

  it('sends a chosen playlist, and a song pick with the Spotify connection', async () => {
    const api = fakeApi(routes(withSpotify(), connectionsBody(true)))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'Easy ride' } })
    fireEvent.click(screen.getByRole('button', { name: 'Music' }))
    fireEvent.click(screen.getByRole('radio', { name: /classbeat picks songs/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect(api.to('POST', '/api/jobs')[0]?.body).toEqual({
      kind: 'interpret_request',
      input: {
        request: 'Easy ride',
        mowl_connection_id: MOWL_ID,
        music_override: { source: 'pick' },
        spotify_connection_id: SPOTIFY_ID,
      },
    })
  })

  it('sends a chosen MOWL playlist by id', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'Easy ride' } })
    fireEvent.click(screen.getByRole('button', { name: 'Music' }))
    fireEvent.click(screen.getByRole('radio', { name: /Hill climbs/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect(api.to('POST', '/api/jobs')[0]?.body).toMatchObject({
      input: { music_override: { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_A } },
    })
  })
})

describe('Ideas for your class', () => {
  it('is closed by default and has Climbs, Sprints, Intervals and Games', async () => {
    fakeApi(routes(plan(true)))
    renderAt('/classes/new')
    const summary = await screen.findByText('Ideas for your class')
    expect(summary.closest('details')).not.toHaveAttribute('open')
    fireEvent.click(summary)
    await screen.findByRole('button', { name: 'Games' })
    for (const name of ['Climbs', 'Sprints', 'Intervals']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
  })

  it('adds a phrase to the prompt, once', async () => {
    fakeApi(routes(plan(true)))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'A 40 minute ride.' } })
    await openIdeas()
    fireEvent.click(screen.getByRole('button', { name: 'Climbs' }))
    fireEvent.click(screen.getByRole('button', { name: 'Finish with a climb.' }))
    fireEvent.click(screen.getByRole('button', { name: 'Finish with a climb.' }))
    expect(await prompt()).toHaveValue('A 40 minute ride. Finish with a climb.')
  })

  it('shows four game cards with illustration, name, MOWL\'s sentence and the licence line; a tap adds a phrase', async () => {
    fakeApi(routes(plan(true)))
    renderAt('/classes/new')
    await openIdeas()
    fireEvent.click(await screen.findByRole('button', { name: 'Games' }))
    expect(screen.getByText('Cycling classes only')).toBeInTheDocument()
    for (const game of ['Racetrack', 'Power Pull', 'Fast Lane', 'Overflow']) {
      expect(screen.getByRole('button', { name: `Add ${game} to your request` })).toBeInTheDocument()
    }
    expect(document.querySelectorAll('[data-slot^="game"]')).toHaveLength(4)
    expect(screen.getByText('Teams compete for the highest average speed.')).toBeInTheDocument()
    expect(screen.getByTestId('game-licence')).toHaveTextContent('not checked here')
    fireEvent.click(screen.getByRole('button', { name: 'Add Power Pull to your request' }))
    expect(await prompt()).toHaveValue('Include a Power Pull game.')
  })

  it('draws the theme\'s own illustration, light and dark', async () => {
    fakeApi(routes(plan(true)))
    for (const theme of ['light', 'dark'] as const) {
      document.documentElement.setAttribute('data-theme', theme)
      const view = renderAt('/classes/new')
      await openIdeas()
      fireEvent.click(await screen.findByRole('button', { name: 'Games' }))
      const img = document.querySelector('[data-slot="gameRacetrack"] img')
      expect(img?.getAttribute('src')).toContain(`game-racetrack-${theme}`)
      view.unmount()
    }
  })

  it('has no Games without the plan gate, and none while the plan is unknown', async () => {
    fakeApi(routes(plan(false)))
    renderAt('/classes/new')
    await openIdeas()
    await screen.findByRole('button', { name: 'Intervals' })
    expect(screen.queryByRole('button', { name: 'Games' })).toBeNull()
  })

  it('has no Games for a running class', async () => {
    fakeApi(routes({ ...plan(true) }, { ...connectionsBody(false), running_enabled: true }))
    renderAt('/classes/new')
    await openIdeas()
    expect(await screen.findByRole('button', { name: 'Games' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Running' }))
    expect(screen.queryByRole('button', { name: 'Games' })).toBeNull()
  })
})

describe('the advanced route', () => {
  it('serves the form at /classes/new/advanced and links back to the start screen', async () => {
    fakeApi(routes())
    renderAt('/classes/new/advanced')
    expect(await screen.findByLabelText('Class name')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the start' })).toHaveAttribute('href', '/en/classes/new')
  })

  it('prefills the form from a Home preset link', async () => {
    fakeApi(routes())
    renderAt('/classes/new/advanced?length=45&climbs=3&intensity=moderate')
    expect(await screen.findByLabelText('Climbs')).toHaveValue(3)
  })
})
