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
  /** The answer is refused with this reason (409). */
  refuse?: string
}

/** A composer class whose sketch is current. The music answer is stored by `PUT`, and the class then reads it back. */
function setup(over: Setup = {}) {
  let decision = over.decision === undefined ? UNDECIDED : over.decision
  let consent = over.consent ?? true
  const spotify = over.spotify ?? true
  const api = fakeApi({
    ...SESSION_ROUTES,
    'GET /api/connections': {
      connections: [{ id: MOWL_CONNECTION, provider: 'mowl', ...OK }, ...(spotify ? [{ id: SPOTIFY_ID, provider: 'spotify', ...OK }] : [])],
      composer_flow: true,
      soundless_enabled: over.soundless ?? false,
    },
    'GET /api/connections/spotify': () => json({ ...STATUS, status: spotify ? 'connected' : 'not_connected', pool_consent: consent }),
    'GET /api/music/mowl-playlists': { playlists: IDS.map((id, i) => ({ spotify_playlist_id: id, name: NAMES[i] })) },
    [`GET ${CLASS_PATH}`]: () => classBody({ sport: 'cycling', composer: true, ...(decision === null ? {} : { music_decision: decision }) }),
    [`GET ${CLASS_PATH}/revisions`]: { revisions: [summaryBody(REV1, 1, { origin: 'sketch' })] },
    [`GET ${CLASS_PATH}/messages`]: { messages: [message(1, 'user', 'Forty-five minutes, three climbs', { job_id: JOB_ID, base_revision_id: null })] },
    [`GET ${CLASS_PATH}/revisions/${REV1}`]: revisionBody(REV1, 1, CONTENT, { origin: 'sketch', provenance: { origin: 'sketch' } }),
    [`GET ${CLASS_PATH}/interpretation`]: { interpret_job_id: JOB_ID, items: [], questions: [] },
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
      'Would you like music for this class? I can choose it for you, you can describe the sound you want or share playlists for inspiration, or we can continue without music.',
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

  it('"Choose it for me" stores a pick, then the search starts once Spotify and the consent are there', async () => {
    const api = setup()
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

  it('shows "Continue without music" off, with a reason, while no-music classes are not on', async () => {
    const api = setup({ soundless: false })
    renderAt(PAGE)
    const button = within(await card()).getByRole('button', { name: 'Continue without music' })
    expect(button).toBeDisabled()
    expect(screen.getByText('Not available here yet')).toBeInTheDocument()
    fireEvent.click(button)
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('starts the same request again as a class with no music when no-music classes are on', async () => {
    const api = setup({ soundless: true })
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
    const api = setup({ spotify: false })
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
    const api = setup({ refuse: 'music_fixed' })
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
