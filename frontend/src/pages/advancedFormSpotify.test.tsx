import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { fakeApi, json, reason } from '../test/fakeApi'
import { SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import { MOWL_ID, NEW_JOB, SPOTIFY_ID, connectionsBody } from '../test/songFixtures'
import { renderAt } from '../test/render'

// The Advanced form follows the start screen's Spotify rules (issue #1138, roadmap Amendments 25
// and 26): a class with music needs Spotify, and the instructor's own playlist becomes a song job
// with that playlist as its source, so the songs are approved and a new private playlist is made.

const PLAYLIST_ID = 'AbCdEfGhIjKlMnOpQrStUv'
const SPOTIFY_STATUS = {
  status: 'connected',
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
  pool_consent: true,
  consent_version: 3,
  consent_text: 'x',
}
const DRAFT_JOB = jobBody('queued')
const BASE = {
  ...SESSION_ROUTES,
  'GET /api/connections': connectionsBody(),
  'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: PLAYLIST_ID, name: 'Hills' }] },
  'GET /api/connections/spotify': SPOTIFY_STATUS,
  [`GET /api/jobs/${NEW_JOB.id}`]: NEW_JOB,
  [`GET /api/jobs/${NEW_JOB.id}/events`]: openStream,
  [`GET /api/jobs/${DRAFT_JOB.id}`]: DRAFT_JOB,
  [`GET /api/jobs/${DRAFT_JOB.id}/events`]: openStream,
}
const SOURCE = { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_ID }

async function submitOnPlaylist() {
  fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Hill day' } })
  fireEvent.click(screen.getByRole('radio', { name: 'Hills' }))
  fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
}

type Posted = { kind: string; input: Record<string, unknown> }

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('the Advanced form on an own Spotify playlist', () => {
  const sourceCases: [string, Partial<typeof SPOTIFY_STATUS>][] = [
    ['with the working playlist allowed', {}],
    ['without the working playlist (a source job needs no pool consent)', { pool_consent: false }],
  ]
  it.each(sourceCases)('starts design_with_songs with the playlist as its source, %s', async (_name, status) => {
    const api = fakeApi({
      ...BASE,
      'GET /api/connections/spotify': { ...SPOTIFY_STATUS, ...status },
      'POST /api/jobs': () => json(NEW_JOB, 201),
    })
    renderAt('/classes/new/advanced')
    await submitOnPlaylist()
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/jobs/${NEW_JOB.id}`)
    })
    const calls = api.to('POST', '/api/jobs')
    expect(calls).toHaveLength(1)
    const body = calls[0]?.body as Posted
    expect(body.kind).toBe('design_with_songs')
    expect(body.input).toMatchObject({
      title: 'Hill day',
      mode: 'pick',
      source: SOURCE,
      mowl_connection_id: MOWL_ID,
      spotify_connection_id: SPOTIFY_ID,
    })
    expect(body.input.music).toBeUndefined()
    expect(screen.queryByTestId('spotify-prompt')).toBeNull()
  })

  it('keeps the write-free design_draft when song picking does not admit the user', async () => {
    const api = fakeApi({
      ...BASE,
      'POST /api/jobs': (body) =>
        (body as Posted).kind === 'design_with_songs' ? reason(409, 'song_picking_off') : json(DRAFT_JOB, 201),
    })
    renderAt('/classes/new/advanced')
    await submitOnPlaylist()
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/jobs/${DRAFT_JOB.id}`)
    })
    const calls = api.to('POST', '/api/jobs')
    expect(calls.map((c) => (c.body as Posted).kind)).toEqual(['design_with_songs', 'design_draft'])
    const draft = calls[1]?.body as Posted
    expect(draft.input).toMatchObject({ title: 'Hill day', music: SOURCE, mowl_connection_id: MOWL_ID })
    expect(draft.input.spotify_connection_id).toBeUndefined()
    // Two requests, two keys: the refused song job's key is never reused for another body.
    const keys = calls.map((c) => c.headers.get('Idempotency-Key'))
    expect(keys[0]).not.toBe(keys[1])
  })

  const refusals: [string, RegExp][] = [
    ['not_connected', /Spotify or MOWL is not connected/],
    ['class_busy', /This class already has a task running/],
  ]
  it.each(refusals)('answers a %s refusal of the song job as it is, with no design job', async (why, text) => {
    const api = fakeApi({ ...BASE, 'POST /api/jobs': () => reason(409, why) })
    renderAt('/classes/new/advanced')
    await submitOnPlaylist()
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(api.to('POST', '/api/jobs').map((c) => (c.body as Posted).kind)).toEqual(['design_with_songs'])
  })

  it('stays the write-free design while Spotify is not offered on the server', async () => {
    const api = fakeApi({
      ...BASE,
      'GET /api/connections/spotify': () => reason(404, 'feature_off'),
      'POST /api/jobs': () => json(DRAFT_JOB, 201),
    })
    renderAt('/classes/new/advanced')
    await submitOnPlaylist()
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/jobs/${DRAFT_JOB.id}`)
    })
    expect(api.to('POST', '/api/jobs').map((c) => (c.body as Posted).kind)).toEqual(['design_draft'])
  })
})

describe('the Advanced form without Spotify', () => {
  const NOT_CONNECTED = {
    'GET /api/connections': connectionsBody(false),
    'GET /api/connections/spotify': { ...SPOTIFY_STATUS, status: 'not_connected' },
  }

  it('says a class with music needs Spotify, links to it, and sends nothing', async () => {
    const api = fakeApi({ ...BASE, ...NOT_CONNECTED })
    renderAt('/classes/new/advanced')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Hill day' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Hills' }))
    expect(screen.getByTestId('spotify-prompt')).toHaveAttribute('data-need', 'connect')
    // One link to connect Spotify: the music choice's own line gives way to the prompt.
    expect(screen.getAllByRole('link', { name: 'Connect Spotify' })).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/en/account/spotify')
    const button = screen.getByRole('button', { name: 'Design my class' })
    expect(button).toBeDisabled()
    fireEvent.submit(button.closest('form')!)
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('lets a soundless class go without Spotify', async () => {
    const api = fakeApi({
      ...BASE,
      ...NOT_CONNECTED,
      'GET /api/connections': connectionsBody(false),
      'POST /api/jobs': () => json(DRAFT_JOB, 201),
    })
    renderAt('/classes/new/advanced')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Quiet day' } })
    fireEvent.click(await screen.findByRole('radio', { name: 'No music' }))
    expect(screen.queryByTestId('spotify-prompt')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/jobs/${DRAFT_JOB.id}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toMatchObject({ kind: 'design_draft', input: { music: { source: 'none' } } })
  })
})
