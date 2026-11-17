import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from '../App'
import { SURPRISES } from '../components/start/surprise'
import { I18nProvider } from '../i18n/I18n'
import { MOWL_ID, SPOTIFY_ID, SPOTIFY_STATUS_OK, connectionsBody } from '../test/songFixtures'
import { fakeApi, json, PLAN_FREE, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// ADR 0038 section 10.1 (#1190): the composer at /classes/new with `composer_flow` on. The API is faked; nothing
// here reaches a network. The flag comes with the connections answer, as `running_enabled` does.

const IDS = ['AbCdEfGhIjKlMnOpQrStU1', 'AbCdEfGhIjKlMnOpQrStU2', 'AbCdEfGhIjKlMnOpQrStU3', 'AbCdEfGhIjKlMnOpQrStU4', 'AbCdEfGhIjKlMnOpQrStU5', 'AbCdEfGhIjKlMnOpQrStU6']
const NAMES = ['Hill climbs', 'Sprint set', 'Walk-in-musik', 'Club classics', 'Run 3', 'Cool down']
const INTERPRET_JOB = jobBody('queued', { kind: 'interpret_request' })

function routes(extra: Record<string, unknown> = {}, connections: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    'GET /api/connections': { ...connectionsBody(true), composer_flow: true, ...connections },
    'GET /api/connections/spotify': SPOTIFY_STATUS_OK,
    'GET /api/music/mowl-playlists': { playlists: IDS.map((id, i) => ({ spotify_playlist_id: id, name: NAMES[i] })) },
    'POST /api/jobs': () => json(INTERPRET_JOB, 201),
    ...extra,
  }
}

const prompt = () => screen.findByLabelText('Describe your class')
const openMenu = async () => {
  await prompt()
  fireEvent.click(screen.getByRole('button', { name: /Add music/ }))
}
const choose = (name: string) => {
  fireEvent.click(within(screen.getByRole('group', { name: 'Add music to your request' })).getByRole('button', { name }))
}
const tags = () => within(screen.getByRole('list', { name: 'Attached music' })).queryAllByRole('listitem')
const tagNames = () => tags().map((li) => li.textContent)
const sent = (api: ReturnType<typeof fakeApi>) => api.to('POST', '/api/jobs')[0]?.body

afterEach(() => {
  window.sessionStorage.clear()
})

describe('the composer flag', () => {
  it('shows the composer with the flag on, and no starter chips, ideas, service chips, destination or playlist list', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    expect(await prompt()).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add music/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Surprise me/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Start/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Advanced settings' })).toHaveAttribute('href', '/en/classes/new/advanced')
    expect(screen.getByText(/favours songs from the music you add, but is not limited to them/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing is written anywhere until you approve/)).toBeInTheDocument()
    expect(screen.queryByText('Ideas for your class')).toBeNull()
    expect(screen.queryByRole('list', { name: 'Connected services' })).toBeNull()
    expect(screen.queryByRole('group', { name: 'Starter ideas' })).toBeNull()
    expect(screen.queryByTestId('destination')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Design my class' })).toBeNull()
    expect(screen.queryByRole('radio')).toBeNull()
  })

  it.each([false, undefined])('shows the old start screen with the flag %s', async (flag) => {
    fakeApi(routes({}, { composer_flow: flag }))
    renderAt('/classes/new')
    expect(await screen.findByRole('button', { name: 'Design my class' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Surprise me/ })).toBeNull()
  })

  it('reads the flag from the connections answer, off when it is missing or not true', async () => {
    const without = connectionsBody(true)
    for (const body of [without, { ...without, composer_flow: 'yes' }, { ...without, composer_flow: 1 }]) {
      fakeApi(routes({ 'GET /api/connections': body }))
      const view = renderAt('/classes/new')
      expect(await screen.findByRole('button', { name: 'Design my class' })).toBeInTheDocument()
      view.unmount()
    }
  })
})

describe('the attached-music tags', () => {
  it('adds a playlist as a tag with its name, keeps the menu open, and removes it with its button', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    await openMenu()
    choose('Hill climbs')
    choose('Sprint set')
    expect(tagNames()).toEqual(['Hill climbsSpotify', 'Sprint setSpotify'])
    expect(screen.getByRole('button', { name: /Add music/ })).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Remove Hill climbs' }))
    expect(tagNames()).toEqual(['Sprint setSpotify'])
  })

  it('removes a playlist by choosing it again in the menu', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    await openMenu()
    choose('Hill climbs')
    expect(screen.getByRole('button', { name: 'Hill climbs' })).toHaveAttribute('aria-pressed', 'true')
    choose('Hill climbs')
    expect(tags()).toHaveLength(0)
  })

  it('allows at most 5 playlists and disables the others until one is removed', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    await openMenu()
    for (const name of NAMES.slice(0, 5)) choose(name)
    expect(tags()).toHaveLength(5)
    const sixth = within(screen.getByRole('group', { name: 'Add music to your request' })).getByRole('button', { name: 'Cool down' })
    expect(sixth).toBeDisabled()
    expect(screen.getByText('You can add up to 5 playlists.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove Run 3' }))
    expect(sixth).toBeEnabled()
    choose('Cool down')
    expect(tags()).toHaveLength(5)
  })

  it('searches the playlists by name, and lists no SwedeBeat mix or playlist', async () => {
    fakeApi(
      routes(
        { 'GET /api/music/swedebeat-mixes': { mixes: [{ mix_id: 'mix-1', name: 'Friday mix', duration_seconds: 2700, analysed: true }] } },
        { swedebeat: { enabled: true, available: true, uses_swedebeat: true }, soundless_enabled: true },
      ),
    )
    renderAt('/classes/new')
    await openMenu()
    const panel = screen.getByRole('group', { name: 'Add music to your request' })
    expect(within(panel).getAllByRole('button')).toHaveLength(NAMES.length + 1)
    expect(within(panel).queryByRole('button', { name: /Friday mix/ })).toBeNull()
    expect(within(panel).queryByText(/SwedeBeat|Swedebeat/i)).toBeNull()
    fireEvent.change(within(panel).getByRole('searchbox', { name: 'Search your playlists' }), { target: { value: 'SPRINT' } })
    expect(within(panel).getByRole('button', { name: 'Sprint set' })).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Hill climbs' })).toBeNull()
    fireEvent.change(within(panel).getByRole('searchbox'), { target: { value: 'zzz' } })
    expect(within(panel).getByText('Nothing matches.')).toBeInTheDocument()
  })

  it('closes the menu with Escape and with a click outside', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    await openMenu()
    const button = screen.getByRole('button', { name: /Add music/ })
    fireEvent.keyDown(screen.getByRole('searchbox', { name: 'Search your playlists' }), { key: 'Escape' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    fireEvent.pointerDown(await prompt())
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('offers "No music" only when it is enabled, as a tag that clears the playlists, and the other way round', async () => {
    fakeApi(routes({}, { soundless_enabled: true }))
    renderAt('/classes/new')
    await openMenu()
    choose('Hill climbs')
    choose('Sprint set')
    choose('No music')
    expect(tagNames()).toEqual(['No music'])
    expect(screen.getByRole('button', { name: 'Hill climbs' })).toHaveAttribute('aria-pressed', 'false')
    choose('Hill climbs')
    expect(tagNames()).toEqual(['Hill climbsSpotify'])
    choose('No music')
    fireEvent.click(screen.getByRole('button', { name: 'Remove No music' }))
    expect(tags()).toHaveLength(0)
  })

  it('has no "No music" while it is not enabled', async () => {
    fakeApi(routes({}, { soundless_enabled: false }))
    renderAt('/classes/new')
    await openMenu()
    expect(within(screen.getByRole('group', { name: 'Add music to your request' })).queryByRole('button', { name: 'No music' })).toBeNull()
  })
})

describe('starting from the composer', () => {
  it('sends the words with the playlists as music_sources, a key and CSRF, then opens the class', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: '  45 minutes, three climbs  ' } })
    fireEvent.click(screen.getByRole('button', { name: /Add music/ }))
    choose('Sprint set')
    choose('Hill climbs')
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/classes/${CLASS_ID}`)
    })
    expect(sent(api)).toEqual({
      kind: 'interpret_request',
      input: {
        request: '45 minutes, three climbs',
        mowl_connection_id: MOWL_ID,
        music_sources: [
          { kind: 'spotify_playlist', id: IDS[1] },
          { kind: 'spotify_playlist', id: IDS[0] },
        ],
        spotify_connection_id: SPOTIFY_ID,
      },
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('sends "No music" as music_override and no music_sources', async () => {
    const api = fakeApi(routes({}, { soundless_enabled: true }))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'A quiet ride' } })
    fireEvent.click(screen.getByRole('button', { name: /Add music/ }))
    choose('Hill climbs')
    choose('No music')
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const input = (sent(api) as { input: Record<string, unknown> }).input
    expect(input.music_override).toEqual({ source: 'none' })
    expect(input).not.toHaveProperty('music_sources')
  })

  it('sends neither music field without tags', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'An easy ride' } })
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const input = (sent(api) as { input: Record<string, unknown> }).input
    expect(input).not.toHaveProperty('music_sources')
    expect(input).not.toHaveProperty('music_override')
  })

  it('wants some words first and sends nothing without them', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new')
    await prompt()
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    expect(await screen.findByText('Describe your class first.')).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('words a refusal in plain language and stays on the page', async () => {
    fakeApi(routes({ 'POST /api/jobs': () => reason(409, 'not_connected') }))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'An easy ride' } })
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/en/classes/new')
  })

  it('opens the advanced form from the Advanced settings link', async () => {
    fakeApi(routes())
    renderAt('/classes/new')
    fireEvent.click(await screen.findByRole('link', { name: 'Advanced settings' }))
    expect(await screen.findByLabelText('Class name')).toBeInTheDocument()
  })
})

describe('the sport select', () => {
  it('is there only when running is enabled', async () => {
    fakeApi(routes())
    const view = renderAt('/classes/new')
    await prompt()
    expect(screen.queryByRole('combobox', { name: 'Sport' })).toBeNull()
    view.unmount()
    fakeApi(routes({}, { running_enabled: true }))
    renderAt('/classes/new')
    expect(await screen.findByRole('combobox', { name: 'Sport' })).toHaveValue('cycling')
  })

  it('sends running, and drops "No music", which a running class does not have', async () => {
    const api = fakeApi(routes({}, { running_enabled: true, soundless_enabled: true }))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'A 5k run' } })
    fireEvent.click(screen.getByRole('button', { name: /Add music/ }))
    choose('No music')
    fireEvent.change(screen.getByRole('combobox', { name: 'Sport' }), { target: { value: 'running' } })
    expect(tags()).toHaveLength(0)
    expect(within(screen.getByRole('group', { name: 'Add music to your request' })).queryByRole('button', { name: 'No music' })).toBeNull()
    choose('Sprint set')
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect(sent(api)).toMatchObject({
      input: { sport_override: 'running', music_sources: [{ kind: 'spotify_playlist', id: IDS[1] }] },
    })
  })
})

describe('Surprise me', () => {
  it('starts a table entry at once, with the attached music, and shows it in the box', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new')
    await openMenu()
    choose('Hill climbs')
    fireEvent.click(screen.getByRole('button', { name: /Surprise me/ }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/classes/${CLASS_ID}`)
    })
    const body = sent(api) as { kind: string; input: { request: string; music_sources: unknown } }
    expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    expect(body.kind).toBe('interpret_request')
    expect(SURPRISES.map((entry) => entry.text.en)).toContain(body.input.request)
    expect(body.input.music_sources).toEqual([{ kind: 'spotify_playlist', id: IDS[0] }])
  })

  it('stays within the plan length', async () => {
    const short = { ...PLAN_FREE, limits: { ...PLAN_FREE.limits, max_class_minutes: 30 } }
    const api = fakeApi(routes({ 'GET /api/account/plan': short }))
    renderAt('/classes/new')
    await screen.findByTestId('plan-length')
    fireEvent.click(screen.getByRole('button', { name: /Surprise me/ }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const request = (sent(api) as { input: { request: string } }).input.request
    const entry = SURPRISES.find((e) => e.text.en === request)
    expect(entry?.minutes).toBeLessThanOrEqual(30)
  })

  it('speaks the page language', async () => {
    const api = fakeApi(routes())
    window.history.replaceState(null, '', '/sv/classes/new')
    render(
      <I18nProvider initial="sv">
        <App />
      </I18nProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: /Överraska mig/ }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    expect(SURPRISES.map((entry) => entry.text.sv)).toContain((sent(api) as { input: { request: string } }).input.request)
  })

  it('still applies the Spotify gate: nothing is sent when song picking is not available', async () => {
    const api = fakeApi(routes({ 'GET /api/connections/spotify': () => reason(404, 'feature_off') }))
    renderAt('/classes/new')
    expect(await screen.findByTestId('spotify-prompt')).toHaveAttribute('data-need', 'unavailable')
    const surprise = screen.getByRole('button', { name: /Surprise me/ })
    expect(surprise).toBeDisabled()
    expect(screen.getByRole('button', { name: /^Start/ })).toBeDisabled()
    fireEvent.click(surprise)
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })
})

describe('the Spotify gate in the composer', () => {
  it('connects Spotify first when no account is connected, and lets "No music" through', async () => {
    const api = fakeApi(routes({}, { ...connectionsBody(false), soundless_enabled: true }))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'An easy ride' } })
    const note = await screen.findByTestId('spotify-prompt')
    expect(note).toHaveAttribute('data-need', 'connect')
    expect(within(note).getByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/en/account/spotify')
    fireEvent.click(screen.getByRole('button', { name: /Add music/ }))
    choose('No music')
    expect(screen.queryByTestId('spotify-prompt')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
  })

  it('shows the prompt for a server refusal instead of an error', async () => {
    fakeApi(routes({ 'POST /api/jobs': () => reason(409, 'needs_spotify') }))
    renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'An easy ride' } })
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    expect(await screen.findByTestId('spotify-prompt')).toHaveAttribute('data-need', 'connect')
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('Try again and examples', () => {
  it('opens again with the same words, playlists and sport after Try again', async () => {
    fakeApi(routes({}, { running_enabled: true }))
    const first = renderAt('/classes/new')
    fireEvent.change(await prompt(), { target: { value: 'Hard hills for Thursday' } })
    fireEvent.click(screen.getByRole('button', { name: /Add music/ }))
    choose('Sprint set')
    choose('Walk-in-musik')
    fireEvent.click(screen.getByRole('button', { name: /^Start/ }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/classes/${CLASS_ID}`)
    })
    first.unmount()
    renderAt(`/classes/new?from=${CLASS_ID}`)
    expect(await prompt()).toHaveValue('Hard hills for Thursday')
    expect(tagNames()).toEqual(['Sprint setSpotify', 'Walk-in-musikSpotify'])
  })

  it('fills the words of an example, and sends nothing until Start', async () => {
    const api = fakeApi(routes())
    renderAt('/classes/new?example=hill-challenge')
    const box = await prompt()
    await waitFor(() => {
      expect((box as HTMLTextAreaElement).value).not.toBe('')
    })
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })
})
