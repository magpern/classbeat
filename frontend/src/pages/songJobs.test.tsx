import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { SONG_FAILURE_CATEGORIES } from '../api/songMessages'
import { classBody, classRoutes, REV1 } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, OPERATION_ID, SESSION_ROUTES, jobBody, openStream, sse } from '../test/jobFixtures'
import {
  CLASS_PATH,
  BRIEF,
  HASH,
  LIST_ID,
  LIST_PATH,
  MOWL_ID,
  NEW_JOB,
  SPOTIFY_ID,
  connectionsBody,
  listBody,
} from '../test/songFixtures'
import { renderAt } from '../test/render'

const JOB = `/api/jobs/${JOB_ID}`
const OPS = `GET /api/external-operations?job_id=${JOB_ID}`

function songJob(state: string, extra: Record<string, unknown> = {}) {
  return jobBody(state, { kind: 'design_with_songs', approval_revision_id: LIST_ID, ...extra })
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('the Designing page of a song pick', () => {
  it('shows the phase-1 stages in words and marks the running one', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: songJob('running'),
      [`GET ${JOB}/events`]: (() => {
        let opened = 0
        return () => (++opened > 1 ? openStream() : sse([1, 'queued'], [2, 'stage_started', { stage: 'pool_write' }]))
      })(),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'Finding songs for your class…' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('progress')).toHaveTextContent('Saving your working playlist')
    })
    const steps = screen.getAllByRole('listitem')
    expect(steps.map((s) => s.textContent)).toEqual([
      'Finding songs that fit (done)',
      'Saving your working playlist',
      'MOWL is listening to the music',
      'Choosing the best songs for each part',
    ])
    expect(steps[1]).toHaveAttribute('aria-current', 'step')
  })

  it('uses the words of the second part after the approval', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: songJob('running', { approved_at: '2026-10-30T10:00:00Z' }),
      [`GET ${JOB}/events`]: () => sse([1, 'stage_started', { stage: 'fill' }]),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('progress')).toHaveTextContent('Adding the approved songs to your playlist')
    })
  })
})

describe('a song job that failed', () => {
  it('has a plain sentence for every category and never shows the code', async () => {
    expect(SONG_FAILURE_CATEGORIES.length).toBeGreaterThanOrEqual(19)
    for (const category of SONG_FAILURE_CATEGORIES) {
      fakeApi({ ...SESSION_ROUTES, [`GET ${JOB}`]: songJob('failed', { error_category: category }) })
      const view = renderAt(`/jobs/${JOB_ID}`)
      const heading = await screen.findByRole('heading', { level: 2 })
      expect(heading.textContent?.length).toBeGreaterThan(8)
      expect(document.body.textContent).not.toContain(category)
      expect(document.body.textContent).not.toContain('Something went wrong on our side')
      view.unmount()
    }
  })

  it('names the Spotify sentences of ADR 0009', async () => {
    const expected: Record<string, RegExp> = {
      invalid_grant: /Spotify no longer accepts the connection\. Reconnect Spotify\./,
      app_misconfigured: /Check the Client ID and the redirect URI/,
      not_allowlisted: /User Management/,
      forbidden: /Spotify refused this request/,
      rate_limited: /slow down/,
      quota_exhausted: /used up its allowance/,
      upstream_unavailable: /could not be reached/,
      account_changed: /not the one these songs were chosen for/,
      playlist_mismatch: /did not match the approved songs; nothing was designed\. Create it again\./,
      pool_too_small: /try a wider style or era/,
      pool_not_synced: /try again in a minute/,
      consent_missing: /Allow the working playlist in your Spotify settings first/,
      pool_unusable: /make it private again or set it up again/,
    }
    for (const [category, pattern] of Object.entries(expected)) {
      fakeApi({ ...SESSION_ROUTES, [`GET ${JOB}`]: songJob('failed', { error_category: category }) })
      const view = renderAt(`/jobs/${JOB_ID}`)
      await waitFor(() => {
        expect(document.body.textContent).toMatch(pattern)
      })
      view.unmount()
    }
  })

  it('prompts to reconnect Spotify for invalid_grant', async () => {
    fakeApi({ ...SESSION_ROUTES, [`GET ${JOB}`]: songJob('failed', { error_category: 'invalid_grant' }) })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('link', { name: 'Reconnect Spotify' })).toHaveAttribute('href', '/account/spotify')
  })

  it('before the approval offers to pick again from the remembered form, not to resume', async () => {
    const brief = { length_min: 45, intensity: 'hard', climbs: 1, sprints: 0, races: 0, ftp_test: null, style: 'hills', text: 'x' }
    window.sessionStorage.setItem(
      `classbeat.pick-input.${JOB_ID}`,
      JSON.stringify({ class_id: CLASS_ID, brief, mode: 'pick', mowl_connection_id: 'old', spotify_connection_id: 'old' }),
    )
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: songJob('failed', { error_category: 'pool_too_small' }),
      'GET /api/connections': connectionsBody(),
      'POST /api/jobs': () => json(NEW_JOB, 201),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(screen.queryByRole('button', { name: 'Create the playlist again' })).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: 'Pick songs again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'design_with_songs',
      input: { class_id: CLASS_ID, brief, mode: 'pick', mowl_connection_id: MOWL_ID, spotify_connection_id: SPOTIFY_ID },
    })
  })

  it('after the approval creates the playlist again with a resume job and no new approval', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: songJob('failed', { error_category: 'playlist_mismatch', approved_at: '2026-10-30T10:00:00Z' }),
      'GET /api/connections': connectionsBody(),
      [`GET ${CLASS_PATH}`]: classBody({ brief: BRIEF }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
      'POST /api/jobs': () => json(NEW_JOB, 201),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Create the playlist again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    const input = (call?.body as { kind: string; input: Record<string, unknown> }).input
    expect((call?.body as { kind: string }).kind).toBe('design_with_songs')
    expect(input.mode).toBe('resume')
    expect(input.resume).toEqual({ song_list_revision_id: LIST_ID })
    expect(input.brief).toEqual(BRIEF)
    expect(input.seed).toBeUndefined()
    expect(api.to('POST', `${JOB}/approve`)).toHaveLength(0)
  })

  it('says why a resume is refused', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: songJob('failed', { error_category: 'playlist_mismatch', approved_at: '2026-10-30T10:00:00Z' }),
      'GET /api/connections': connectionsBody(),
      [`GET ${CLASS_PATH}`]: classBody({ brief: BRIEF }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
      'POST /api/jobs': () => reason(409, 'receipt_invalid'),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Create the playlist again' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/cannot be turned into a playlist again/)
  })

  it('shows the uncertain panel for a final-playlist write and offers Create the playlist again once decided', async () => {
    const operation = {
      id: OPERATION_ID,
      kind: 'spotify_create_playlist',
      state: 'uncertain',
      resolved_by: null,
      class_id: CLASS_ID,
      actionable_at: null,
    }
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: songJob('failed', { error_category: 'uncertain_write', approved_at: '2026-10-30T10:00:00Z' }),
      [OPS]: { operations: [operation] },
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: { ...operation, state: 'failed', resolved_by: 'reconciler' },
      'GET /api/connections': connectionsBody(),
      [`GET ${CLASS_PATH}`]: classBody({ brief: BRIEF }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
      'POST /api/jobs': () => json(NEW_JOB, 201),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText(/could not tell whether your playlist was written in Spotify/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create the playlist again' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(/Nothing was written to Spotify/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create the playlist again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    expect(api.to('POST', '/api/jobs')).toHaveLength(1)
  })

  it('asks for confirmation before Do it anyway', async () => {
    const operation = {
      id: OPERATION_ID,
      kind: 'spotify_replace_items',
      state: 'uncertain',
      resolved_by: null,
      class_id: CLASS_ID,
      actionable_at: null,
    }
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: songJob('failed', { error_category: 'uncertain_write', approved_at: '2026-10-30T10:00:00Z' }),
      [OPS]: { operations: [operation] },
      [`POST /api/external-operations/${OPERATION_ID}/do-it-anyway`]: { ...operation, state: 'failed', resolved_by: 'user' },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Do it anyway' }))
    expect(screen.getByText(/two playlists of the same name/)).toBeInTheDocument()
    expect(api.to('POST', `/api/external-operations/${OPERATION_ID}/do-it-anyway`)).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, it was not written' }))
    expect(await screen.findByRole('button', { name: 'Create the playlist again' })).toBeInTheDocument()
  })

  it('says a stopped or expired list wrote nothing', async () => {
    fakeApi({ ...SESSION_ROUTES, [`GET ${JOB}`]: songJob('expired') })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('These songs expired')).toBeInTheDocument()
    expect(screen.getByText(/Nothing was written to your Spotify/)).toBeInTheDocument()
  })
})

describe('the new-class form', () => {
  const READY = {
    ...SESSION_ROUTES,
    'GET /api/connections': connectionsBody(),
    'GET /api/music/mowl-playlists': { playlists: [] },
    'GET /api/connections/spotify': {
      status: 'connected',
      last_error: null,
      last_error_message: null,
      callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
      pool_consent: true,
      consent_version: 1,
      consent_text: 'x',
    },
  }

  const ONE = { playlists: [{ spotify_playlist_id: 'AbCdEfGhIjKlMnOpQrStUv', name: 'Hills' }] }

  it('is hidden when song picking is off', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      'GET /api/connections': connectionsBody(false),
      'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: 'AbCdEfGhIjKlMnOpQrStUv', name: 'Hills' }] },
    })
    renderAt('/classes/new')
    await screen.findByLabelText('Class name')
    expect(screen.queryByRole('button', { name: 'Pick songs for me' })).toBeNull()
  })

  it('links to the wizard without Spotify, and to the consent without it', async () => {
    fakeApi({
      ...READY,
      'GET /api/music/mowl-playlists': ONE,
      'GET /api/connections': connectionsBody(false),
      'GET /api/connections/spotify': { ...(READY['GET /api/connections/spotify'] as object), status: 'not_connected' },
    })
    renderAt('/classes/new')
    const pick = await screen.findByRole('button', { name: 'Pick songs for me' })
    expect(pick).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/account/spotify')
  })

  it('asks for the working playlist consent first', async () => {
    fakeApi({
      ...READY,
      'GET /api/music/mowl-playlists': ONE,
      'GET /api/connections/spotify': { ...(READY['GET /api/connections/spotify'] as object), pool_consent: false },
    })
    renderAt('/classes/new')
    expect(await screen.findByRole('button', { name: 'Pick songs for me' })).toBeDisabled()
    expect(screen.getByText(/Allow the working playlist in your Spotify settings first/)).toBeInTheDocument()
  })

  it('starts a pick without a playlist, with both own connection ids', async () => {
    const api = fakeApi({ ...READY, 'POST /api/jobs': () => json(NEW_JOB, 201), [`GET /api/jobs/${NEW_JOB.id}`]: NEW_JOB, [`GET /api/jobs/${NEW_JOB.id}/events`]: openStream })
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Songs class' } })
    expect(screen.getByLabelText('Playlist')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Pick songs for me' }))
    expect(screen.queryByLabelText('Playlist')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    const body = call?.body as { kind: string; input: Record<string, unknown> }
    expect(body.kind).toBe('design_with_songs')
    expect(body.input).toMatchObject({
      title: 'Songs class',
      mode: 'pick',
      mowl_connection_id: MOWL_ID,
      spotify_connection_id: SPOTIFY_ID,
    })
    expect(body.input.music).toBeUndefined()
  })

  it('words the refusals of a pick', async () => {
    fakeApi({ ...READY, 'POST /api/jobs': () => reason(409, 'pool_busy') })
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Songs class' } })
    fireEvent.click(screen.getByRole('button', { name: 'Pick songs for me' }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Another song pick is still running')
  })
})

describe('the class page', () => {
  const SONGS = { state: 'approved', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }

  it('shows the own playlist link only as a Spotify playlist link', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: {
          songs: SONGS,
          playlist: { name: 'Climb <b>mix</b>', link: 'https://open.spotify.com/playlist/AbCdEfGhIjKlMnOpQrStUv' },
        },
      }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
    })
    renderAt(`/classes/${CLASS_ID}`)
    const link = await screen.findByRole('link', { name: 'Open in Spotify' })
    expect(link).toHaveAttribute('href', 'https://open.spotify.com/playlist/AbCdEfGhIjKlMnOpQrStUv')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByTestId('playlist')).toHaveTextContent('Climb <b>mix</b>')
  })

  it('drops a link that is not a Spotify playlist link', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({ cls: { songs: SONGS, playlist: { name: 'Mix', link: 'https://evil.example/x' } } }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
    })
    renderAt(`/classes/${CLASS_ID}`)
    expect(await screen.findByTestId('playlist')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open in Spotify' })).toBeNull()
  })

  it('has no Change songs for a class whose music Claude did not pick', async () => {
    fakeApi({ ...SESSION_ROUTES, ...classRoutes() })
    renderAt(`/classes/${CLASS_ID}`)
    await screen.findByText('Tuesday climb')
    expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
  })

  it('starts revise_songs for the marked songs from the current version', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({ cls: { songs: SONGS } }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
      'GET /api/connections': connectionsBody(),
      'POST /api/jobs': () => json({ ...NEW_JOB, kind: 'revise_songs' }, 201),
    })
    renderAt(`/classes/${CLASS_ID}`)
    const region = await screen.findByRole('region', { name: 'Change songs' })
    fireEvent.click(await screen.findByRole('button', { name: 'Change the marked songs' }))
    expect(await screen.findByText('Write what you want different about the songs.')).toBeInTheDocument()
    fireEvent.change(region.querySelector('textarea')!, { target: { value: 'Faster please' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change the marked songs' }))
    expect(await screen.findByText('Mark the songs to replace, or replace all of them.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Replace Hills <b>bold</b>' }))
    fireEvent.click(screen.getByRole('button', { name: 'Change the marked songs' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'revise_songs',
      input: {
        class_id: CLASS_ID,
        base_revision_id: REV1,
        replace: [0],
        request: 'Faster please',
        mode: 'pick',
        mowl_connection_id: MOWL_ID,
        spotify_connection_id: SPOTIFY_ID,
      },
    })
    expect(CLASS_PATH).toContain(CLASS_ID)
  })

  it('can change all songs, and words not_song_picked', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({ cls: { songs: SONGS } }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
      'GET /api/connections': connectionsBody(),
      'POST /api/jobs': () => reason(409, 'not_song_picked'),
    })
    renderAt(`/classes/${CLASS_ID}`)
    const region = await screen.findByRole('region', { name: 'Change songs' })
    await screen.findByRole('button', { name: 'Change all songs' })
    fireEvent.change(region.querySelector('textarea')!, { target: { value: 'Everything new' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change all songs' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/was not picked by Claude/)
    const input = (api.to('POST', '/api/jobs')[0]?.body as { input: Record<string, unknown> }).input
    expect(input.replace).toBe('all')
  })
})

describe('a song job with an undecided MOWL attach', () => {
  const ATTACH_ID = '77777777-7777-4777-8777-777777777777'
  const attach = {
    id: ATTACH_ID,
    kind: 'mowl_attach_spotify_playlist',
    state: 'uncertain',
    resolved_by: null,
    class_id: CLASS_ID,
    actionable_at: null,
  }
  const playlist = { ...attach, id: OPERATION_ID, kind: 'spotify_create_playlist' }
  const failedJob = songJob('failed', { error_category: 'uncertain_write', approved_at: '2026-10-30T10:00:00Z' })

  it('shows the attach panel with its sentence and holds the resume back until it is decided', async () => {
    let decided = false
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failedJob,
      [OPS]: () => ({ operations: decided ? [] : [attach] }),
      [`POST /api/external-operations/${ATTACH_ID}/check-again`]: () => {
        decided = true
        return { ...attach, state: 'succeeded' }
      },
      'GET /api/connections': connectionsBody(),
      [`GET ${CLASS_PATH}`]: classBody({ brief: BRIEF }),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
      'POST /api/jobs': () => json(NEW_JOB, 201),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeInTheDocument()
    expect(screen.getByText(/MOWL did not answer clearly/)).toBeInTheDocument()
    expect(screen.queryByText(/written in Spotify/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Create the playlist again' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Create the playlist again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    expect(api.to('POST', '/api/jobs')).toHaveLength(1)
  })

  it('holds the resume back until both a playlist write and an attach are decided', async () => {
    let attachDecided = false
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failedJob,
      [OPS]: () => ({ operations: attachDecided ? [playlist] : [playlist, attach] }),
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: { ...playlist, state: 'failed', resolved_by: 'reconciler' },
      [`POST /api/external-operations/${ATTACH_ID}/check-again`]: () => {
        attachDecided = true
        return { ...attach, state: 'succeeded' }
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText(/written in Spotify/)).toBeInTheDocument()
    expect(await screen.findByText(/MOWL did not answer clearly/)).toBeInTheDocument()
    const resume = () => screen.queryByRole('button', { name: 'Create the playlist again' })
    const checks = screen.getAllByRole('button', { name: 'Check again' })
    expect(checks).toHaveLength(2)
    fireEvent.click(checks[0]!) // the playlist write
    await screen.findByText(/Nothing was written to Spotify/)
    expect(resume()).toBeNull()
    fireEvent.click(screen.getAllByRole('button', { name: 'Check again' })[1]!) // the attach
    expect(await screen.findByRole('button', { name: 'Create the playlist again' })).toBeInTheDocument()
  })
})

describe('creating the playlist again for a revise_songs list', () => {
  it('sends the contract shape: the current draft as base, the list, and nothing to change', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('failed', {
        kind: 'revise_songs',
        approval_revision_id: LIST_ID,
        approved_at: '2026-10-30T10:00:00Z',
        error_category: 'playlist_mismatch',
      }),
      ...classRoutes(),
      [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
      'GET /api/connections': connectionsBody(),
      'POST /api/jobs': () => json({ ...NEW_JOB, kind: 'revise_songs' }, 201),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Create the playlist again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    expect(api.to('POST', '/api/jobs')[0]?.body).toEqual({
      kind: 'revise_songs',
      input: {
        class_id: CLASS_ID,
        base_revision_id: REV1,
        mode: 'resume',
        resume: { song_list_revision_id: LIST_ID },
        mowl_connection_id: MOWL_ID,
        spotify_connection_id: SPOTIFY_ID,
      },
    })
  })
})

describe('the refusals of starting a song job', () => {
  const REFUSALS: [string, RegExp][] = [
    ['stale_base', /class changed in the meantime/],
    ['revision_limit', /most versions/],
    ['not_song_picked', /not picked by Claude/],
    ['receipt_invalid', /cannot be turned into a playlist again/],
    ['chat_full', /conversation of this class is full/],
    ['consent_missing', /Allow the working playlist/],
    ['pool_busy', /Another song pick is still running/],
    ['account_changed', /not the one these songs were chosen for/],
    ['write_unresolved', /earlier step on Spotify is still undecided/],
  ]
  for (const [code, text] of REFUSALS) {
    it(`words ${code} when a change is started`, async () => {
      const SONGS = { state: 'approved', job_id: JOB_ID, revision_id: LIST_ID, content_hash: HASH, expires_at: null }
      fakeApi({
        ...SESSION_ROUTES,
        ...classRoutes({ cls: { songs: SONGS } }),
        [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
        'GET /api/connections': connectionsBody(),
        'POST /api/jobs': () => reason(409, code),
      })
      renderAt(`/classes/${CLASS_ID}`)
      const region = await screen.findByRole('region', { name: 'Change songs' })
      await screen.findByRole('button', { name: 'Change all songs' })
      fireEvent.change(region.querySelector('textarea')!, { target: { value: 'Different' } })
      fireEvent.click(screen.getByRole('button', { name: 'Change all songs' }))
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent(text)
      expect(alert.textContent).not.toContain(code)
    })
  }
})
