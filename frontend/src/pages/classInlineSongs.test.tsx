import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CLASS_PATH as CLS, classRoutes } from '../test/classFixtures'
import { fakeApi, json } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import {
  BRIEF,
  LIST_ID,
  LIST_PATH,
  NEW_JOB,
  connectionsBody,
  listBody,
  waitingJob,
} from '../test/songFixtures'
import { renderAt } from '../test/render'
import { recallRequest } from '../lib/startRequest'

// MA4 (#884): the song approval, the planned songs of each section and Change music, all inside the
// class workspace. The API is faked; nothing here reaches a network.

const PAGE = `/classes/${CLASS_ID}`
const JOB = `/api/jobs/${JOB_ID}`
const WAITING = { id: JOB_ID, kind: 'design_with_songs', state: 'waiting_for_approval' }
const NO_VERSION = { current_revision_id: null, current_revision_number: null, revision_count: 0 }
const PLAYLIST = 'AbCdEfGhIjKlMnOpQrStUv'
const SPOTIFY_OK = {
  status: 'connected',
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
  pool_consent: true,
  consent_version: 1,
  consent_text: 'x',
}
const APPROVED = { state: 'approved', job_id: JOB_ID, revision_id: LIST_ID, content_hash: 'ab'.repeat(32), expires_at: null }

/** A new class whose song pick waits for approval (no version yet). */
function waitingRoutes(extra: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { ...NO_VERSION, open_job: WAITING, brief: BRIEF }, revisions: [] }),
    [`GET ${JOB}`]: waitingJob(),
    [`GET ${JOB}/events`]: openStream,
    [`GET ${LIST_PATH}`]: listBody(),
    ...extra,
  }
}

/** A designed class whose songs classbeat picked and the user approved. */
function pickedRoutes(extra: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { songs: APPROVED, brief: BRIEF } }),
    [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
    'GET /api/connections': connectionsBody(),
    'GET /api/connections/spotify': SPOTIFY_OK,
    'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: PLAYLIST, name: 'Hill climbs' }] },
    ...extra,
  }
}

afterEach(() => {
  window.sessionStorage.clear()
})

describe('song approval inside the workspace', () => {
  it('lists the songs in the Class panel, under the page title, without a link to the job page', async () => {
    fakeApi(waitingRoutes())
    renderAt(PAGE)
    const work = await screen.findByRole('region', { name: 'Class' })
    const inline = await within(work).findByTestId('inline-songs')
    expect(within(inline).getByRole('heading', { level: 2, name: 'Songs for your class' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(within(inline).getAllByTestId('song')).toHaveLength(2)
    expect(within(inline).getByText(/2 songs, 6:00 in total\. You asked for 5:00\./)).toBeInTheDocument()
    // Titles stay text.
    expect(within(inline).getByText('Hills <b>bold</b>')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Review the songs' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Open the class' })).toBeNull()
    // The class opens on its Class tab, where the songs are.
    expect(screen.getByRole('tab', { name: 'Class' })).toHaveAttribute('aria-selected', 'true')
  })

  it('approves with the revision id and the hash only, then shows the designing state in place', async () => {
    let approved = false
    const running = jobBody('queued', { kind: 'design_with_songs', approved_at: '2026-10-30T10:00:00Z' })
    const api = fakeApi(
      waitingRoutes({
        [`GET ${JOB}`]: () => (approved ? running : waitingJob()),
        [`POST ${JOB}/approve`]: () => {
          approved = true
          return json(running)
        },
      }),
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve songs' }))
    await waitFor(() => {
      expect(api.to('POST', `${JOB}/approve`)).toHaveLength(1)
    })
    expect(api.to('POST', `${JOB}/approve`)[0]?.body).toEqual({ revision_id: LIST_ID, content_hash: 'ab'.repeat(32) })
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
    expect(window.location.pathname).toBe(`/en${PAGE}`)
    expect(screen.queryByRole('button', { name: 'Approve songs' })).toBeNull()
  })

  it('is not sticky: Approve and the expiry sit in the panel, and there is one Approve', async () => {
    fakeApi(waitingRoutes())
    renderAt(PAGE)
    const inline = await screen.findByTestId('inline-songs')
    const group = within(inline).getByRole('group', { name: 'Approval' })
    expect(within(group).getByRole('button', { name: 'Approve songs' })).toBeInTheDocument()
    expect(within(group).getByTestId('expiry')).toHaveTextContent(/^Approve by /)
    expect(screen.getAllByRole('button', { name: 'Approve songs' })).toHaveLength(1)
  })

  it('asks for changes on the class: stops the waiting list, starts a new pick and stays on the class', async () => {
    const api = fakeApi(
      waitingRoutes({
        [`POST ${JOB}/cancel`]: jobBody('cancelled', { kind: 'design_with_songs' }),
        'GET /api/connections': connectionsBody(),
        'POST /api/jobs': () => json(NEW_JOB, 201),
      }),
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Hills <b>bold</b>' }))
    fireEvent.change(screen.getByLabelText('What to change? (optional)'), { target: { value: 'More climbing songs' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask for changes' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const input = (api.to('POST', '/api/jobs')[0]?.body as { input: Record<string, unknown> }).input
    expect(input).toMatchObject({
      class_id: CLASS_ID,
      mode: 'pick',
      seed: { song_list_revision_id: LIST_ID, replace: [0] },
    })
    expect(api.to('POST', `${JOB}/approve`)).toHaveLength(0)
    // The class is read again (it now has the new job) and the page does not move.
    await waitFor(() => {
      expect(api.to('GET', CLS).length).toBeGreaterThan(1)
    })
    expect(window.location.pathname).toBe(`/en${PAGE}`)
  })

  it('says a replaced list is replaced, with a Refresh that reads the class again', async () => {
    const api = fakeApi(waitingRoutes({ [`GET ${LIST_PATH}`]: listBody({ status: 'superseded' }) }))
    renderAt(PAGE)
    expect(await screen.findByText('A newer list replaced these songs')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve songs' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Open the class' })).toBeNull()
    const before = api.to('GET', CLS).length
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => {
      expect(api.to('GET', CLS).length).toBeGreaterThan(before)
    })
  })

  it('shows a refusal as it is and offers no second Approve once it is final', async () => {
    fakeApi(
      waitingRoutes({
        [`POST ${JOB}/approve`]: () => json({ detail: { reason: 'expired' } }, 409),
      }),
    )
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve songs' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/These songs expired before they were approved/)
    expect(screen.queryByRole('button', { name: 'Approve songs' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Open the class' })).toBeNull()
  })

  it('shows the list of a Change songs request on a class that has a version, above the version', async () => {
    fakeApi({
      ...waitingRoutes(),
      ...classRoutes({ cls: { open_job: { ...WAITING, kind: 'revise_songs' }, songs: APPROVED } }),
      [`GET ${JOB}`]: waitingJob({ kind: 'revise_songs' }),
    })
    renderAt(PAGE)
    const inline = await screen.findByTestId('inline-songs')
    const version = await screen.findByRole('article')
    expect(inline.compareDocumentPosition(version) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve songs' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask for changes' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Stop and change' })).toBeInTheDocument()
  })

  it('points a song job that ended at the job page and does not say nothing was written', async () => {
    fakeApi(waitingRoutes({ [`GET ${JOB}`]: waitingJob({ state: 'failed', approved_at: '2026-10-30T10:00:00Z' }) }))
    renderAt(PAGE)
    expect(await screen.findByText(/The songs did not finish\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See what happened' })).toHaveAttribute('href', `/en/jobs/${JOB_ID}`)
    expect(screen.queryByText(/Nothing was saved/)).toBeNull()
  })
})

describe('planned songs per section', () => {
  it('draws one strip per section the songs reach, with the songs as text and no claim about the music', async () => {
    fakeApi(pickedRoutes())
    renderAt(PAGE)
    const planned = await screen.findByTestId('planned-songs')
    const sections = within(planned).getAllByTestId('planned-section')
    // Warm up 0:00 to 5:00 holds both songs (the second starts at 3:52); Climb holds the second as it runs on; Cool down has none.
    expect(sections).toHaveLength(2)
    expect(within(sections[0]!).getByRole('heading', { level: 4 })).toHaveTextContent('Warm up')
    expect(within(sections[0]!).getAllByRole('listitem').map((n) => n.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining('Hills <b>bold</b>'), expect.stringContaining('Rolling <i>hills</i>')]),
    )
    expect(within(sections[1]!).getByRole('heading', { level: 4 })).toHaveTextContent('Climb')
    expect(within(sections[1]!).getAllByText(/Rolling <i>hills<\/i>/).length).toBeGreaterThan(0)
    expect(within(sections[1]!).queryAllByText(/Hills <b>bold<\/b>/)).toHaveLength(0)
    expect(within(planned).getAllByTestId('song-strip')).toHaveLength(2)
    expect(within(planned).getByText(/This is the plan, not a measurement of the music\./)).toBeInTheDocument()
    expect(planned.textContent).not.toMatch(/energy|mood|danceab/i)
  })

  it('is not drawn for a class without an approved song list', async () => {
    fakeApi({ ...SESSION_ROUTES, ...classRoutes() })
    renderAt(PAGE)
    await screen.findByRole('article')
    expect(screen.queryByTestId('planned-songs')).toBeNull()
  })
})

describe('Change music', () => {
  const open = async () => {
    fireEvent.click(await screen.findByRole('button', { name: 'Change music' }))
  }

  it('asks nothing of the services until it is opened', async () => {
    const api = fakeApi(pickedRoutes())
    renderAt(PAGE)
    await screen.findByTestId('change-music')
    expect(api.to('GET', '/api/music/mowl-playlists')).toHaveLength(0)
    await open()
    await screen.findByRole('button', { name: 'Music' })
    expect(api.to('GET', '/api/music/mowl-playlists')).toHaveLength(1)
  })

  it('leads "classbeat picks songs" to the Change songs panel, which takes focus', async () => {
    const api = fakeApi(pickedRoutes())
    renderAt(PAGE)
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Music' }))
    fireEvent.click(screen.getByRole('radio', { name: /classbeat picks songs/ }))
    expect(screen.getByTestId('change-music-next')).toHaveTextContent(/Change songs replaces some or all of them/)
    fireEvent.click(screen.getByRole('button', { name: 'Go to Change songs' }))
    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'Change songs' })).toHaveFocus()
    })
    expect(api.calls.filter((c) => c.method !== 'GET')).toHaveLength(0)
  })

  it('sends any other source to the start screen with the request kept, and writes nothing', async () => {
    const api = fakeApi({
      ...pickedRoutes(),
      ...classRoutes({
        cls: { songs: APPROVED, brief: BRIEF },
        messages: [
          {
            seq: 1,
            role: 'user',
            body: 'A hilly 45 minute ride',
            job_id: null,
            base_revision_id: null,
            result_revision_id: null,
            change: null,
            created_at: '2026-10-29T10:30:00Z',
          },
        ],
      }),
    })
    renderAt(PAGE)
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Music' }))
    fireEvent.click(screen.getByRole('radio', { name: /Hill climbs/ }))
    expect(screen.getByTestId('change-music-next')).toHaveTextContent(/a new design/)
    expect(screen.queryByRole('button', { name: 'Go to Change songs' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open the start screen with this music' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/en/classes/new')
    })
    expect(window.location.search).toBe(`?from=${CLASS_ID}`)
    expect(recallRequest(CLASS_ID)).toEqual({ request: 'A hilly 45 minute ride', music: `playlist:${PLAYLIST}`, sport: 'cycling' })
    expect(api.calls.filter((c) => c.method !== 'GET')).toHaveLength(0)
  })

  it('does not offer "classbeat picks songs" as a Change songs shortcut for a class that has none to change', async () => {
    fakeApi({ ...pickedRoutes(), ...classRoutes({ cls: { songs: { ...APPROVED, state: 'none', revision_id: null } } }) })
    renderAt(PAGE)
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Music' }))
    fireEvent.click(screen.getByRole('radio', { name: /classbeat picks songs/ }))
    expect(screen.getByTestId('change-music-next')).toHaveTextContent(/a new design/)
    expect(screen.queryByRole('button', { name: 'Go to Change songs' })).toBeNull()
  })

  it('waits while the class has a job open', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({ cls: { open_job: { id: JOB_ID, kind: 'refit_draft', state: 'running' } } }),
    })
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Change music' })).toBeDisabled()
    expect(screen.getByText(/Wait for the work on this class to finish/)).toBeInTheDocument()
  })

  it('is not there before the class has a version', async () => {
    fakeApi(waitingRoutes())
    renderAt(PAGE)
    await screen.findByTestId('inline-songs')
    expect(screen.queryByTestId('change-music')).toBeNull()
  })
})
