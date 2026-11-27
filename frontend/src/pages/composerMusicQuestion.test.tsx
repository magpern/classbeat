import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CLASS_PATH, CONTENT, MOWL_CONNECTION, REV1, classBody, message, revisionBody, summaryBody } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { renderAt } from '../test/render'
import { SPOTIFY_ID, SPOTIFY_STATUS_OK } from '../test/songFixtures'

// The music question beside the sketch and the consent before the first song search (ADR 0038 amendment of
// 2026-11-24, decisions 5 to 8; #1496). The API is faked: no Spotify, MOWL or model is reached.

const PAGE = `/classes/${CLASS_ID}`
const MUSIC = `${CLASS_PATH}/music`
const FILL_PATH = `${CLASS_PATH}/fill-songs`
const CONSENT = '/api/connections/spotify/pool-consent'
const OK = { status: 'connected', last_error: null, connected_at: '2026-10-28T10:00:00Z', last_used_at: null }
const IDS = ['AbCdEfGhIjKlMnOpQrStU1', 'AbCdEfGhIjKlMnOpQrStU2', 'AbCdEfGhIjKlMnOpQrStU3']
const NAMES = ['Hill climbs', 'Sprint set', 'Club classics']
const UNDECIDED = { state: 'undecided', choice: null, playlists: 0 }
const DECIDED = { state: 'decided', choice: 'pick', playlists: 0 }
const STATUS = { ...SPOTIFY_STATUS_OK, consent_version: 2, consent_text: 'The working playlist sentence.' }

interface Setup {
  decision?: Record<string, unknown> | null
  spotify?: boolean
  consent?: boolean
  soundless?: boolean
  /** The reading has a hint of what to play (a song the instructor named): only then "Choose it for me" is offered. */
  pinned?: boolean
  /** The answer is refused with this reason (409). */
  refuse?: string
  /** The consent card's Spotify status (the second read) is held back until the test lets it go. */
  statusGate?: Promise<void>
  /** Mutable: the next read of `GET /api/connections` answers with this (a connection made in another tab, or a failure). */
  connections?: { spotify: boolean; fail: boolean }
}

/** A composer class whose sketch is current. The music answer is stored by `PUT`, and the class then reads it back. */
function setup(over: Setup = {}) {
  let decision = over.decision === undefined ? UNDECIDED : over.decision
  let consent = over.consent ?? true
  const spotify = over.spotify ?? true
  let statusReads = 0
  const api = fakeApi({
    ...SESSION_ROUTES,
    'GET /api/connections': () => {
      const now = over.connections ?? { spotify, fail: false }
      if (now.fail) return reason(500, 'boom')
      return json({
        connections: [{ id: MOWL_CONNECTION, provider: 'mowl', ...OK }, ...(now.spotify ? [{ id: SPOTIFY_ID, provider: 'spotify', ...OK }] : [])],
        composer_flow: true,
        // A stale server may still send the retired flag (#1543): the page must ignore it.
        ...(over.soundless === undefined ? {} : { soundless_enabled: over.soundless }),
      })
    },
    'GET /api/connections/spotify': async () => {
      // The first read is the availability check; the second one is the consent card's own status.
      statusReads += 1
      if (statusReads > 1) await over.statusGate
      return json({ ...STATUS, status: spotify ? 'connected' : 'not_connected', pool_consent: consent })
    },
    'GET /api/music/mowl-playlists': { playlists: IDS.map((id, i) => ({ spotify_playlist_id: id, name: NAMES[i] })) },
    [`GET ${CLASS_PATH}`]: () => classBody({ sport: 'cycling', composer: true, ...(decision === null ? {} : { music_decision: decision }) }),
    [`GET ${CLASS_PATH}/revisions`]: { revisions: [summaryBody(REV1, 1, { origin: 'sketch' })] },
    [`GET ${CLASS_PATH}/messages`]: { messages: [message(1, 'user', 'Forty-five minutes, three climbs', { job_id: JOB_ID, base_revision_id: null })] },
    [`GET ${CLASS_PATH}/revisions/${REV1}`]: revisionBody(REV1, 1, CONTENT, { origin: 'sketch', provenance: { origin: 'sketch' } }),
    [`GET ${CLASS_PATH}/interpretation`]: {
      interpret_job_id: JOB_ID,
      items: [],
      questions: [],
      pinned_songs: over.pinned === true ? [{ title: 'Africa', artist: 'Toto' }] : [],
    },
    [`GET ${CLASS_PATH}/pending-messages`]: { messages: [] },
    [`PUT ${MUSIC}`]: (body) => {
      if (over.refuse !== undefined) return reason(409, over.refuse)
      const choice = (body as { choice: string }).choice
      decision = choice === 'later' ? UNDECIDED : { state: 'decided', choice, playlists: 0 }
      return json({ state: (decision as { state: string }).state, choice, playlists: 0 })
    },
    [`PUT ${CONSENT}`]: () => {
      consent = true
      return new Response(null, { status: 204 })
    },
    [`POST ${FILL_PATH}`]: { job_id: 'f1111111-1111-4111-8111-111111111111' },
    'POST /api/jobs': () => json(jobBody('queued', { kind: 'interpret_request' }), 201),
  })
  return api
}

const fills = (api: ReturnType<typeof setup>) => api.to('POST', FILL_PATH)
const puts = (api: ReturnType<typeof setup>) => api.to('PUT', MUSIC)
const card = () => screen.findByTestId('music-question')
const answer = async (name: string) => {
  fireEvent.click(within(await card()).getByRole('button', { name }))
}
/** Gives a started fill time to show: the driver would have called by now. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 60))

afterEach(() => {
  cleanup()
})

describe('the music question', () => {
  it('is shown while the music is undecided, with the question, and starts no song search', async () => {
    const api = setup()
    renderAt(PAGE)
    const question = await card()
    expect(question).toHaveTextContent(
      'What music would you like for this class? Name a style, describe the sound you want or share playlists for inspiration, or we can continue without music.',
    )
    await settle()
    expect(fills(api)).toHaveLength(0)
  })

  it('is shown again after closing and reopening the class, until it is answered', async () => {
    const api = setup()
    const first = renderAt(PAGE)
    await card()
    first.unmount()
    renderAt(PAGE)
    await card()
    await settle()
    expect(fills(api)).toHaveLength(0)
    expect(puts(api)).toHaveLength(0)
  })

  it('offers no "Choose it for me" without a hint of what to play: styles, a typed style, playlists and no music', async () => {
    setup()
    renderAt(PAGE)
    const question = await card()
    expect(within(question).queryByRole('button', { name: 'Choose it for me' })).toBeNull()
    for (const name of ['Describe the sound', 'Share playlists', 'Continue without music']) {
      expect(within(question).getByRole('button', { name })).toBeInTheDocument()
    }
  })

  it('sends the chosen styles and the typed style as the described sound', async () => {
    const api = setup()
    renderAt(PAGE)
    await answer('Describe the sound')
    fireEvent.click(await screen.findByRole('button', { name: '80s' }))
    fireEvent.click(screen.getByRole('button', { name: 'Rock' }))
    expect(screen.getByLabelText('The sound you want')).toHaveValue('80s, Rock')
    fireEvent.change(screen.getByLabelText('The sound you want'), { target: { value: '80s, Rock, no vocals' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this' }))
    await waitFor(() => {
      expect(puts(api)).toHaveLength(1)
    })
    expect(puts(api)[0]!.body).toEqual({ choice: 'describe', note: '80s, Rock, no vocals' })
  })

  it('says why when the server refuses a pick with no hint (the same words as the hand-off)', async () => {
    const api = setup({ pinned: true, refuse: 'music_unanswered' })
    renderAt(PAGE)
    await answer('Choose it for me')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Tell us what music you want, or choose No music, and no songs are picked until you do.',
    )
    expect(puts(api)).toHaveLength(1)
  })

  it('"Choose it for me" stores a pick, then the search starts once Spotify and the consent are there', async () => {
    const api = setup({ pinned: true })
    renderAt(PAGE)
    await answer('Choose it for me')
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(puts(api)[0]!.body).toEqual({ choice: 'pick' })
    expect(fills(api)[0]!.body).toEqual({ base_revision_id: REV1 })
    expect(screen.queryByTestId('music-question')).toBeNull()
  })

  it('"Describe the sound" sends the one-line note', async () => {
    const api = setup()
    renderAt(PAGE)
    await answer('Describe the sound')
    const box = await screen.findByLabelText('The sound you want')
    expect(screen.getByRole('button', { name: 'Use this' })).toBeDisabled()
    fireEvent.change(box, { target: { value: '  80s hits, nothing too loud ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this' }))
    await waitFor(() => {
      expect(puts(api)).toHaveLength(1)
    })
    expect(puts(api)[0]!.body).toEqual({ choice: 'describe', note: '80s hits, nothing too loud' })
  })

  it('"Share playlists" sends the chosen Spotify playlists, at most five, with the existing picker', async () => {
    const api = setup()
    renderAt(PAGE)
    await answer('Share playlists')
    const send = await screen.findByRole('button', { name: 'Share these playlists' })
    expect(send).toBeDisabled()
    fireEvent.click(within(await card()).getByRole('button', { name: /Add music/ }))
    const menu = screen.getByRole('group', { name: 'Add music to your request' })
    expect(within(menu).queryByRole('button', { name: 'No music' })).toBeNull()
    fireEvent.click(within(menu).getByRole('button', { name: 'Hill climbs' }))
    fireEvent.click(within(menu).getByRole('button', { name: 'Club classics' }))
    fireEvent.click(screen.getByRole('button', { name: 'Share these playlists' }))
    await waitFor(() => {
      expect(puts(api)).toHaveLength(1)
    })
    expect(puts(api)[0]!.body).toEqual({
      choice: 'playlists',
      playlists: [
        { kind: 'spotify_playlist', id: IDS[0] },
        { kind: 'spotify_playlist', id: IDS[2] },
      ],
    })
  })

  it('"Decide later" stores it, closes the card for now and starts no search; reopening shows it again', async () => {
    const api = setup()
    const first = renderAt(PAGE)
    await answer('Decide later')
    await waitFor(() => {
      expect(screen.queryByTestId('music-question')).toBeNull()
    })
    expect(puts(api)[0]!.body).toEqual({ choice: 'later' })
    await settle()
    expect(fills(api)).toHaveLength(0)
    first.unmount()
    renderAt(PAGE)
    await card()
  })

  it.each([
    ['an answer with no flag', undefined],
    ['a stale answer that still says off', false],
  ] as const)('always offers "Continue without music" and starts the request with no music (%s, #1543)', async (_, soundless) => {
    // Before #1543 the button was off, with "Not available here yet", while `soundless_enabled` was off.
    const api = setup(soundless === undefined ? {} : { soundless })
    renderAt(PAGE)
    const button = within(await card()).getByRole('button', { name: 'Continue without music' })
    expect(screen.queryByText('Not available here yet')).toBeNull()
    expect(button).toBeEnabled()
    fireEvent.click(button)
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const body = api.to('POST', '/api/jobs')[0]!.body as { kind: string; input: Record<string, unknown> }
    expect(body.kind).toBe('interpret_request')
    expect(body.input).toMatchObject({ request: 'Forty-five minutes, three climbs', music_override: { source: 'none' }, mowl_connection_id: MOWL_CONNECTION })
    expect(body.input).not.toHaveProperty('spotify_connection_id')
    expect(puts(api)).toHaveLength(0)
  })

  it('keeps the options without Spotify, with a link to connect it, and stores the answer either way', async () => {
    const api = setup({ spotify: false, pinned: true })
    renderAt(PAGE)
    const question = await card()
    expect(within(question).getByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/en/account/spotify')
    fireEvent.click(within(question).getByRole('button', { name: 'Choose it for me' }))
    await waitFor(() => {
      expect(puts(api)).toHaveLength(1)
    })
    const connect = await screen.findByTestId('music-connect')
    expect(within(connect).getByRole('link', { name: 'Connect Spotify' })).toBeInTheDocument()
    await settle()
    expect(fills(api)).toHaveLength(0)
  })

  it('says why when the answer is refused because the songs are already being chosen', async () => {
    const api = setup({ refuse: 'music_fixed', pinned: true })
    renderAt(PAGE)
    await answer('Choose it for me')
    expect(await screen.findByRole('alert')).toHaveTextContent('The songs are already being chosen, so the music can no longer be changed here.')
    expect(puts(api)).toHaveLength(1)
    expect(screen.getByTestId('music-question')).toBeInTheDocument()
  })
})

describe('the consent before the first song search', () => {
  it('shows what classbeat will write, with the consent switch, and starts no search while it is off', async () => {
    const api = setup({ decision: DECIDED, consent: false })
    renderAt(PAGE)
    const consent = await screen.findByTestId('music-consent')
    expect(consent).toHaveTextContent(
      'classbeat keeps one public working playlist in your Spotify account with candidate songs and links it in MOWL, so it can analyse them. The final playlist and the workout are created only when you approve.',
    )
    expect(within(consent).getByTestId('spotify-consent-text')).toHaveTextContent('The working playlist sentence.')
    await settle()
    expect(fills(api)).toHaveLength(0)
    expect(screen.queryByTestId('music-question')).toBeNull()
  })

  it('starts the song search once the switch is on', async () => {
    const api = setup({ decision: DECIDED, consent: false })
    renderAt(PAGE)
    const consent = await screen.findByTestId('music-consent')
    fireEvent.click(within(consent).getByRole('switch', { name: 'Allow the working playlist' }))
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(api.to('PUT', CONSENT)).toHaveLength(1)
    await waitFor(() => {
      expect(screen.queryByTestId('music-consent')).toBeNull()
    })
  })

  it('keeps a placeholder where the card goes while the consent status loads', async () => {
    let release: () => void = () => undefined
    const api = setup({ decision: DECIDED, consent: false, statusGate: new Promise<void>((resolve) => { release = resolve }) })
    renderAt(PAGE)
    expect(await screen.findByTestId('music-consent-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('music-consent')).toBeNull()
    release()
    expect(await screen.findByTestId('music-consent')).toBeInTheDocument()
    expect(screen.queryByTestId('music-consent-loading')).toBeNull()
    expect(fills(api)).toHaveLength(0)
  })

  it('reads Spotify and the consent again when the window gets focus, and starts the search once they are there', async () => {
    const connections = { spotify: false, fail: false }
    // The status route answers as connected once the connection exists.
    const api = setup({ decision: DECIDED, spotify: true, connections })
    renderAt(PAGE)
    expect(await screen.findByTestId('music-connect')).toBeInTheDocument()
    expect(fills(api)).toHaveLength(0)
    // Connected in another tab: the next read of the connections shows it.
    connections.spotify = true
    window.dispatchEvent(new Event('focus'))
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(screen.queryByTestId('music-connect')).toBeNull()
  })

  it('reads again when the tab becomes visible, and keeps what it showed when that read fails', async () => {
    const connections = { spotify: true, fail: false }
    const api = setup({ decision: DECIDED, consent: false, connections })
    renderAt(PAGE)
    await screen.findByTestId('music-consent')
    const reads = api.to('GET', '/api/connections').length
    connections.fail = true
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => {
      expect(api.to('GET', '/api/connections').length).toBeGreaterThan(reads)
    })
    await settle()
    expect(screen.getByTestId('music-consent')).toBeInTheDocument()
    expect(screen.queryByText(/Choosing songs is not available on your account/)).toBeNull()
  })

  it('shows no consent card while the music is undecided', async () => {
    setup({ consent: false })
    renderAt(PAGE)
    await card()
    expect(screen.queryByTestId('music-consent')).toBeNull()
  })

  it('starts the search at once for a decided class with Spotify and the consent', async () => {
    const api = setup({ decision: DECIDED })
    renderAt(PAGE)
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(screen.queryByTestId('music-question')).toBeNull()
    expect(screen.queryByTestId('music-consent')).toBeNull()
  })

  it('waits with a decided class and no Spotify, saying how to connect it', async () => {
    const api = setup({ decision: DECIDED, spotify: false })
    renderAt(PAGE)
    expect(await screen.findByTestId('music-connect')).toBeInTheDocument()
    await settle()
    expect(fills(api)).toHaveLength(0)
  })

  it('never asks a class with no decision (an older class), and keeps its fill as before', async () => {
    const api = setup({ decision: null })
    renderAt(PAGE)
    await waitFor(() => {
      expect(fills(api)).toHaveLength(1)
    })
    expect(screen.queryByTestId('music-question')).toBeNull()
    expect(api.to('GET', '/api/connections/spotify')).toHaveLength(0)
  })
})
