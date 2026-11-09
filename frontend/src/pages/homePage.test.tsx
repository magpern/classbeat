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
  it('greets the user and shows recent classes as rows', async () => {
    home({ 'GET /api/connections': { connections: [MOWL] } })
    renderAt('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Tuesday climb' })).toHaveAttribute('href', expect.stringMatching(/^\/en\/classes\//))
    expect(screen.getByRole('img', { name: /Class profile/ })).toBeInTheDocument()
    const recent = screen.getByRole('region', { name: 'Recent classes' })
    expect(within(recent).getAllByRole('listitem')).toHaveLength(1)
    expect(within(recent).getByRole('link', { name: 'See all' })).toHaveAttribute('href', '/en/classes')
  })

  it('has one primary Design a class action and quick starts that link to the form', async () => {
    home()
    renderAt('/')
    const design = await screen.findByRole('link', { name: 'Design a class' })
    expect(design).toHaveAttribute('href', '/en/classes/new')
    expect(screen.getAllByRole('link', { name: 'Design a class' })).toHaveLength(1)
    const presets = screen.getByRole('list', { name: 'Quick start' })
    const links = within(presets).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['45 min hills', '30 min intervals', '60 min endurance'])
    expect(links[0]).toHaveAttribute('href', '/en/classes/new?length=45&climbs=3&intensity=moderate')
  })

  it('says so when there are no classes yet', async () => {
    home({ 'GET /api/classes': { classes: [] } })
    renderAt('/')
    expect(await screen.findByText(/No classes yet/)).toBeInTheDocument()
    // a calm decorative picture beside the text: empty alt, lazy, size set
    const img = document.querySelector('main [data-slot="emptyHome"] img')
    expect(img).toHaveAttribute('alt', '')
    expect(img).toHaveAttribute('loading', 'lazy')
    expect(img).toHaveAttribute('width', '768')
    expect(img).toHaveAttribute('height', '576')
  })

  it('shows no empty-state picture once there are classes', async () => {
    home()
    renderAt('/')
    await screen.findByRole('link', { name: 'Tuesday climb' })
    expect(document.querySelector('[data-slot="emptyHome"]')).toBeNull()
  })

  it('offers to try again when the list fails', async () => {
    home({ 'GET /api/classes': json({}, 500) })
    renderAt('/')
    expect(await screen.findByText('Your classes could not be loaded.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('Get set up', () => {
  it('lists MOWL while it is not connected, and Spotify as optional when song picking is on', async () => {
    home({ 'GET /api/connections/spotify': SPOTIFY('not_connected') })
    renderAt('/')
    const region = await screen.findByRole('region', { name: 'Get set up' })
    const rows = within(region).getAllByRole('listitem')
    expect(rows.map((r) => r.textContent)).toEqual([
      'MMOWLNeeded to save classes as workoutsConnect',
      'SSpotifyOptionalPicks songs for your classesConnect',
    ])
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'Connect MOWL' })).toHaveAttribute('href', '/en/account')
    expect(within(rows[1] as HTMLElement).getByRole('link', { name: 'Connect Spotify' })).toHaveAttribute(
      'href',
      '/en/account/spotify',
    )
  })

  it('has no Spotify row with song picking off, and says MOWL needs connecting again when it does', async () => {
    home({
      'GET /api/connections': { connections: [{ ...MOWL, status: 'needs_reconnect' }] },
      'GET /api/connections/spotify': SPOTIFY_OFF,
    })
    renderAt('/')
    const region = await screen.findByRole('region', { name: 'Get set up' })
    expect(within(region).getByText('Needs reconnecting')).toBeInTheDocument()
    expect(within(region).queryByText(/Spotify/)).toBeNull()
  })

  it('shows only what is still missing', async () => {
    home({ 'GET /api/connections': { connections: [MOWL] }, 'GET /api/connections/spotify': SPOTIFY('not_connected') })
    renderAt('/')
    const region = await screen.findByRole('region', { name: 'Get set up' })
    expect(within(region).getAllByRole('listitem')).toHaveLength(1)
    expect(within(region).queryByText('MOWL')).toBeNull()
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
      length: '30', climbs: '0', sprints: '0', races: '0', intensity: 'moderate',
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
    expect(await screen.findByRole('radio', { name: '30 min' })).toBeChecked()
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
    expect(await screen.findByRole('radio', { name: '60 min' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Easy' })).toBeChecked()
  })
})
