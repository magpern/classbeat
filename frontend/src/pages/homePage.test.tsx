import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { PRESETS, presetLink, readPreset } from '../lib/presets'
import { classBody } from '../test/classFixtures'
import { SIGNED_IN, fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'

const MOWL = { id: 'c1', provider: 'mowl', status: 'connected', last_error: null, connected_at: '2026-10-01T00:00:00Z', last_used_at: null }
const SPOTIFY_OFF = () => json({ detail: { reason: 'feature_off' } }, 404)
const SPOTIFY = (status: string) => ({
  status,
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1/oauth/spotify/callback',
  pool_consent: false,
  consent_version: null,
  consent_text: 'x',
})

function home(extra: Record<string, unknown> = {}) {
  return fakeApi({
    'GET /api/auth/session': SIGNED_IN,
    'GET /api/classes': { classes: [classBody()] },
    ...extra,
  })
}

describe('Home', () => {
  it('greets the user and shows recent classes as cards', async () => {
    home({ 'GET /api/connections': { connections: [MOWL] } })
    renderAt('/')
    expect(await screen.findByRole('heading', { name: 'Welcome back, ann' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Tuesday climb' })).toHaveAttribute('href', expect.stringMatching(/^\/classes\//))
    expect(screen.getByRole('img', { name: /Class profile/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See all your classes' })).toHaveAttribute('href', '/classes')
  })

  it('has one primary Design a class action and presets that link to the form', async () => {
    home()
    renderAt('/')
    const region = await screen.findByRole('region', { name: 'Design a new class' })
    expect(within(region).getByRole('link', { name: 'Design a class' })).toHaveAttribute('href', '/classes/new')
    const presets = within(region).getByRole('list', { name: 'Start from a preset' })
    const links = within(presets).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['45 min hills', '30 min intervals', '60 min endurance'])
    expect(links[0]).toHaveAttribute('href', '/classes/new?length=45&climbs=3&intensity=moderate')
  })

  it('says so when there are no classes yet', async () => {
    home({ 'GET /api/classes': { classes: [] } })
    renderAt('/')
    expect(await screen.findByText(/No classes yet/)).toBeInTheDocument()
  })

  it('offers to try again when the list fails', async () => {
    home({ 'GET /api/classes': json({}, 500) })
    renderAt('/')
    expect(await screen.findByText('Your classes could not be loaded.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('Get set up checklist', () => {
  it('asks to connect MOWL while it is not connected, and Spotify as optional when song picking is on', async () => {
    home({ 'GET /api/connections/spotify': SPOTIFY('not_connected') })
    renderAt('/')
    const region = await screen.findByRole('region', { name: 'Get set up' })
    const items = within(region).getAllByRole('listitem')
    expect(items.map((i) => i.textContent)).toEqual([
      'Create your account, done',
      'Connect MOWL, not done yetConnect',
      'Connect Spotify (optional), not done yetConnect',
    ])
    expect(within(items[1] as HTMLElement).getByRole('link', { name: 'Connect' })).toHaveAttribute('href', '/account')
    expect(within(items[2] as HTMLElement).getByRole('link', { name: 'Connect' })).toHaveAttribute('href', '/account/spotify')
  })

  it('has no Spotify step with song picking off, and asks to connect MOWL again when it needs it', async () => {
    home({
      'GET /api/connections': { connections: [{ ...MOWL, status: 'needs_reconnect' }] },
      'GET /api/connections/spotify': SPOTIFY_OFF,
    })
    renderAt('/')
    const region = await screen.findByRole('region', { name: 'Get set up' })
    expect(within(region).getByText(/Connect MOWL again/)).toBeInTheDocument()
    expect(within(region).queryByText(/Spotify/)).toBeNull()
  })

  it('is gone once everything is set up', async () => {
    home({ 'GET /api/connections': { connections: [MOWL] }, 'GET /api/connections/spotify': SPOTIFY_OFF })
    renderAt('/')
    await screen.findByRole('link', { name: 'Tuesday climb' })
    expect(screen.queryByRole('region', { name: 'Get set up' })).toBeNull()
  })

  it('stays hidden when the connections cannot be read', async () => {
    home({ 'GET /api/connections': json({}, 500) })
    renderAt('/')
    await screen.findByRole('link', { name: 'Tuesday climb' })
    expect(screen.queryByRole('region', { name: 'Get set up' })).toBeNull()
  })
})

describe('presets', () => {
  it('readPreset takes only values in range and the three intensity words', () => {
    const q = (s: string) => new URLSearchParams(s)
    expect(readPreset(q('length=30&sprints=6&intensity=hard'))).toEqual({
      length: '30', climbs: '0', sprints: '6', races: '0', intensity: 'hard',
    })
    expect(readPreset(q('length=9999&climbs=-1&sprints=x&races=6&intensity=extreme'))).toEqual({
      length: '45', climbs: '0', sprints: '0', races: '0', intensity: 'moderate',
    })
  })

  it('every preset link round-trips through readPreset', () => {
    for (const preset of PRESETS) {
      const query = new URLSearchParams(presetLink(preset).split('?')[1])
      const read = readPreset(query)
      expect(read.length).toBe(preset.params.length)
    }
  })

  it('a preset link prefills the New class form', async () => {
    fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'GET /api/connections': { connections: [MOWL] },
      'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: 'p1', name: 'Mix' }] },
    })
    renderAt('/classes/new?length=30&sprints=6&intensity=hard')
    expect(await screen.findByLabelText('Length in minutes')).toHaveValue(30)
    expect(screen.getByLabelText('Sprints')).toHaveValue(6)
    expect(screen.getByLabelText('Climbs')).toHaveValue(0)
    expect(screen.getByRole('radio', { name: 'Hard' })).toBeChecked()
  })

  it('the link on Home opens the form with those values', async () => {
    fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'GET /api/classes': { classes: [] },
      'GET /api/connections': { connections: [MOWL] },
      'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: 'p1', name: 'Mix' }] },
    })
    renderAt('/')
    await userEvent.click(await screen.findByRole('link', { name: '60 min endurance' }))
    expect(await screen.findByLabelText('Length in minutes')).toHaveValue(60)
    expect(screen.getByRole('radio', { name: 'Easy' })).toBeChecked()
  })
})
