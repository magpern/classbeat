import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { readPreset } from '../lib/presets'
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

  it('has an Afterburn title block: eyebrow, display title and one intro line', async () => {
    home()
    renderAt('/')
    const title = await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    expect(title.parentElement).toHaveTextContent('Home')
    expect(title.parentElement).toHaveTextContent('Design a class in a minute')
  })

  it('has only the Design a class action in the hero card, with no quick-start list', async () => {
    home()
    renderAt('/')
    const hero = await screen.findByRole('region', { name: 'Design a class' })
    expect(within(hero).queryByRole('list')).toBeNull()
    expect(within(hero).getAllByRole('link')).toHaveLength(1)
    expect(within(hero).getByRole('link', { name: 'Design a class' })).toHaveAttribute('href', '/en/classes/new')
  })

  it('puts a trash can on every class, with what stays in MOWL and Swedebeat in the confirmation', async () => {
    const SECOND = '22222222-2222-4222-8222-222222222222'
    const empty = classBody({ id: SECOND, title: 'Never designed', current_revision_id: null, current_revision_number: null, profile: [] })
    const saved = classBody({
      id: '33333333-3333-4333-8333-333333333333',
      title: 'Saved one',
      saved: { count: 1, latest_revision_number: 1, latest_saved_at: '2026-10-30T10:00:00Z', latest_up_to_date: true },
      swedebeat_written: true,
    })
    let remaining = [empty, saved]
    const api = home({
      'GET /api/classes': () => ({ classes: remaining }),
      [`DELETE /api/classes/${SECOND}`]: () => {
        remaining = remaining.filter((c) => c.id !== SECOND)
        return { deleted: true, mowl_workouts_kept: 0, note: 'x' }
      },
    })
    renderAt('/')
    // A saved, Swedebeat-written class: the dialog says both stay; nothing is deleted yet.
    await userEvent.click(await screen.findByRole('button', { name: 'Delete class Saved one' }))
    expect(await screen.findByText(/stays there; delete it in MOWL/)).toBeInTheDocument()
    expect(screen.getByText(/cannot be deleted from here/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Keep it' }))
    // An unsaved class with nothing in Swedebeat: no Swedebeat warning; it is discarded.
    await userEvent.click(screen.getByRole('button', { name: 'Delete class Never designed' }))
    expect(screen.queryByText(/cannot be deleted from here/)).toBeNull()
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, delete' }))
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Never designed' })).toBeNull()
    })
    expect(api.calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Saved one' })).toBeInTheDocument()
  })

  it('has one primary Design a class action that links to the start screen', async () => {
    home()
    renderAt('/')
    const design = await screen.findByRole('link', { name: 'Design a class' })
    expect(design).toHaveAttribute('href', '/en/classes/new')
    expect(screen.getAllByRole('link', { name: 'Design a class' })).toHaveLength(1)
    expect(screen.queryByRole('list', { name: 'Quick start' })).toBeNull()
  })

  it('says so when there are no classes yet', async () => {
    home({ 'GET /api/classes': { classes: [] } })
    renderAt('/')
    expect(await screen.findByText(/No classes yet/)).toBeInTheDocument()
    // a card like My classes, under the section heading, with no "See all"
    const recent = screen.getByRole('region', { name: 'Recent classes' })
    expect(within(recent).queryByRole('link', { name: 'See all' })).toBeNull()
    expect(within(recent).queryAllByRole('listitem')).toHaveLength(0)
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
  it('lists MOWL while it is not connected, and Spotify when song picking is on', async () => {
    home({ 'GET /api/connections/spotify': SPOTIFY('not_connected') })
    renderAt('/')
    const region = await screen.findByRole('region', { name: 'Get set up' })
    const rows = within(region).getAllByRole('listitem')
    expect(rows.map((r) => r.textContent)).toEqual([
      'MMOWLNeeded to save classes as workoutsConnect',
      'SSpotifyPicks songs for your classesConnect',
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

describe('the form prefill', () => {
  it('readPreset takes only values in range and the three intensity words', () => {
    const q = (s: string) => new URLSearchParams(s)
    expect(readPreset(q('length=30&sprints=6&intensity=hard'))).toEqual({
      length: '30', climbs: '0', sprints: '6', races: '0', intensity: 'hard',
    })
    expect(readPreset(q('length=9999&climbs=-1&sprints=x&races=6&intensity=extreme'))).toEqual({
      length: '30', climbs: '0', sprints: '0', races: '0', intensity: 'moderate',
    })
  })

  it('the query string prefills the New class form', async () => {
    fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'GET /api/connections': { connections: [MOWL] },
      'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: 'p1', name: 'Mix' }] },
    })
    renderAt('/classes/new/advanced?length=30&sprints=6&intensity=hard')
    expect(await screen.findByRole('radio', { name: '30 min' })).toBeChecked()
    expect(screen.getByLabelText('Sprints')).toHaveValue(6)
    expect(screen.getByLabelText('Climbs')).toHaveValue(0)
    expect(screen.getByRole('radio', { name: 'Hard' })).toBeChecked()
  })
})
