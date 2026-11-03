import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ProfileChart } from '../components/classes/ProfileChart'
import type { DraftSegment } from '../api/jobs'
import { MOWL_CONNECTION, classRoutes, message, revisionBody, REV1 } from '../test/classFixtures'
import { fakeApi, json } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, PLAYLIST_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import { renderAt } from '../test/render'

const MOWL = {
  id: MOWL_CONNECTION,
  provider: 'mowl',
  status: 'connected',
  last_error: null,
  connected_at: '2026-10-28T10:00:00Z',
  last_used_at: null,
  expires_at: null,
}
const PLAYLISTS = { playlists: [{ spotify_playlist_id: PLAYLIST_ID, name: 'Tempo run' }] }

function newClassRoutes(connections: Record<string, unknown>) {
  return {
    ...SESSION_ROUTES,
    'GET /api/connections': { connections: [MOWL], ...connections },
    'GET /api/music/mowl-playlists': PLAYLISTS,
    'POST /api/jobs': () => json(jobBody('queued'), 201),
    [`GET /api/jobs/${JOB_ID}`]: jobBody('running'),
    [`GET /api/jobs/${JOB_ID}/events`]: openStream,
  }
}

afterEach(() => {
  window.sessionStorage.clear()
})

describe('the new-class form and running', () => {
  it('has no sport control and no running copy while the flag is off', async () => {
    fakeApi(newClassRoutes({}))
    renderAt('/classes/new')
    await screen.findByLabelText('Class name')
    expect(screen.queryByRole('button', { name: 'Running' })).toBeNull()
    expect(screen.queryByText('Sport')).toBeNull()
    expect(document.body).not.toHaveTextContent(/running/i)
    expect(screen.getByLabelText('Climbs')).toBeInTheDocument()
  })

  it('offers Cycling and Running with Cycling chosen when the flag is on', async () => {
    fakeApi(newClassRoutes({ running_enabled: true }))
    renderAt('/classes/new')
    await screen.findByLabelText('Class name')
    expect(screen.getByRole('button', { name: 'Cycling' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Running' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByLabelText('FTP test')).toBeInTheDocument()
  })

  it('hides the bike-only knobs and the song choices for Running and sends a running brief', async () => {
    const api = fakeApi(
      newClassRoutes({
        running_enabled: true,
        swedebeat: { enabled: false, available: false },
      }),
    )
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Tempo run' } })
    fireEvent.click(screen.getByRole('button', { name: 'Running' }))
    expect(screen.getByRole('button', { name: 'Running' })).toHaveAttribute('aria-pressed', 'true')
    for (const label of ['Climbs', 'Sprints', 'Races', 'FTP test']) {
      expect(screen.queryByLabelText(label)).toBeNull()
    }
    expect(screen.queryByRole('button', { name: 'Pick songs for me' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Use my Swedebeat mix' })).toBeNull()
    expect(screen.getByRole('heading', { name: 'The run' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Tempo run' }))
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '30' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Hard' }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${JOB_ID}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'design_draft',
      input: {
        title: 'Tempo run',
        brief: { sport: 'running', length_min: 30, intensity: 'hard', style: '', text: '' },
        music: { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_ID },
        mowl_connection_id: MOWL_CONNECTION,
      },
    })
  })

  it('goes back to the cycling form and brief when Cycling is chosen again', async () => {
    const api = fakeApi(newClassRoutes({ running_enabled: true }))
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Back' } })
    fireEvent.click(screen.getByRole('button', { name: 'Running' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cycling' }))
    expect(screen.getByLabelText('Climbs')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Tempo run' }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const input = (api.to('POST', '/api/jobs')[0]?.body as { input: { brief: Record<string, unknown> } }).input
    expect(input.brief).not.toHaveProperty('sport')
    expect(input.brief).toHaveProperty('climbs', 0)
  })
})

const RUNNING: DraftSegment[] = [
  {
    name: 'Warm up',
    kind: 'warmup',
    intervals: [{ sec: 300, zone: 1, rpm: null, standing: false, gradient: 0, cue: '' }],
  },
  {
    name: 'Hills',
    kind: 'main',
    intervals: [
      { sec: 120, zone: 4, rpm: null, standing: false, gradient: 1, cue: 'Up we go' },
      { sec: 60, zone: 5, rpm: null, standing: false, gradient: 0, cue: '' },
    ],
  },
]

describe('the running profile chart', () => {
  it('shows five zones, the incline of the chosen interval and no flame, bolt or position', () => {
    const { container } = render(<ProfileChart segments={RUNNING} title="Hills" songs={null} />)
    expect(container.querySelectorAll('[data-bar]')).toHaveLength(3)
    expect(screen.queryByText('Z6')).toBeNull()
    expect(screen.getByText('Z5')).toBeInTheDocument()
    expect(screen.queryByTestId('marker-flame')).toBeNull()
    expect(screen.queryByTestId('marker-bolt')).toBeNull()
    fireEvent.click(container.querySelector('[data-bar="1"]')!)
    const detail = screen.getByTestId('profile-detail')
    expect(detail).toHaveTextContent('Zone 4')
    expect(detail).toHaveTextContent('incline')
    expect(detail).toHaveTextContent('Up we go')
    expect(detail).not.toHaveTextContent(/rpm|seated|standing/)
  })
})

const RUNNING_CONTENT = {
  name: 'Tempo run',
  description: '',
  segments: [
    {
      name: 'Warm up',
      kind: 'warmup',
      intervals: [{ sec: 300, zone: 1, gradient: 0, cue: '' }],
    },
    {
      name: 'Hills',
      kind: 'main',
      intervals: [
        { sec: 120, zone: 4, gradient: 1, cue: 'Up' },
        { sec: 60, zone: 5, gradient: 0, cue: '' },
      ],
    },
  ],
}

function runningClass(extra: Record<string, unknown> = {}, messages: unknown[] = []) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { sport: 'running', ...extra }, messages }),
    [`GET /api/classes/${CLASS_ID}/revisions/${REV1}`]: revisionBody(REV1, 1, RUNNING_CONTENT, {
      unknown_bpm: [],
    }),
    'GET /api/connections': { connections: [MOWL], running_enabled: true },
  }
}

describe('a running class page', () => {
  it('names the sport and shows incline instead of cadence and position, without TSS', async () => {
    fakeApi(runningClass())
    renderAt(`/classes/${CLASS_ID}`)
    expect(await screen.findByTestId('sport')).toHaveTextContent('Running')
    const table = (await screen.findAllByRole('region', { name: 'Hills' }))[0]!
    expect(within(table).getByRole('columnheader', { name: 'Incline' })).toBeInTheDocument()
    expect(within(table).queryByRole('columnheader', { name: /Cadence|Position/ })).toBeNull()
    expect(within(table).getByText('Incline', { selector: 'td' })).toBeInTheDocument()
    expect(within(table).getByText('Flat')).toBeInTheDocument()
    expect(screen.queryByTestId('tss')).toBeNull()
    expect(screen.getByText('Z5 VO2max')).toBeInTheDocument()
    expect(screen.queryByText(/^Z6/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Change songs' })).toBeNull()
  })

  it('shows no sport chip for a cycling class', async () => {
    fakeApi({ ...SESSION_ROUTES, ...classRoutes() })
    renderAt(`/classes/${CLASS_ID}`)
    await screen.findByTestId('tss')
    expect(screen.queryByTestId('sport')).toBeNull()
  })

  it('words a music-only request for running without offering Change songs', async () => {
    const REVISE_JOB = 'b1111111-1111-4111-8111-111111111111'
    fakeApi({
      ...runningClass({}, [message(1, 'user', 'Different songs please')]),
      [`GET /api/jobs/${REVISE_JOB}`]: jobBody('failed', { id: REVISE_JOB, error_category: 'use_change_songs' }),
    })
    renderAt(`/classes/${CLASS_ID}`)
    const outcome = await screen.findByTestId('use-change-songs')
    expect(outcome).toHaveTextContent('A running class uses a playlist from your MOWL account')
    expect(outcome).not.toHaveTextContent('Use Change songs')
    expect(within(outcome).queryByRole('button')).toBeNull()
  })
})

describe('a design that ended feature_off', () => {
  const INPUT = {
    title: 'Run',
    brief: { sport: 'running', length_min: 30, intensity: 'moderate', style: '', text: '' },
    music: { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_ID },
    mowl_connection_id: MOWL_CONNECTION,
  }
  const failed = () =>
    fakeApi({
      ...SESSION_ROUTES,
      [`GET /api/jobs/${JOB_ID}`]: jobBody('failed', { error_category: 'feature_off' }),
    })

  it('says running is switched off for a running class, never Swedebeat', async () => {
    window.sessionStorage.setItem(`classbeat.design-input.${JOB_ID}`, JSON.stringify(INPUT))
    failed()
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('Running classes are switched off right now')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(/swedebeat/i)
  })

  it('keeps the Swedebeat sentence for a Swedebeat mix', async () => {
    window.sessionStorage.setItem(
      `classbeat.design-input.${JOB_ID}`,
      JSON.stringify({
        ...INPUT,
        brief: { length_min: 30, intensity: 'moderate', climbs: 0, sprints: 0, races: 0, ftp_test: null, style: '', text: '' },
        music: { source: 'mowl_swedebeat_mix', mix_id: 'mix-1' },
      }),
    )
    failed()
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('Swedebeat is switched off right now')).toBeInTheDocument()
  })

  it('is neutral when the page does not know what was designed', async () => {
    failed()
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('This is switched off right now')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(/swedebeat/i)
  })
})
