import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { classBody, classRoutes } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import {
  BRIEF,
  CLASS_PATH,
  HASH,
  LIST_ID,
  LIST_PATH,
  MOWL_ID,
  NEW_JOB,
  SPOTIFY_ID,
  connectionsBody,
  listBody,
  waitingJob,
} from '../test/songFixtures'
import { timing } from '../lib/songs'
import { renderAt } from '../test/render'

const JOB = `/api/jobs/${JOB_ID}`

function approvalRoutes(list = listBody(), job = waitingJob()) {
  return {
    ...SESSION_ROUTES,
    [`GET ${JOB}`]: job,
    [`GET ${JOB}/events`]: openStream,
    [`GET ${LIST_PATH}`]: list,
  }
}

beforeEach(() => {
  window.sessionStorage.clear()
  timing.classBusyPauseMs = 1
})

describe('the Songs for approval page', () => {
  it('shows role, BPM, length, the total against the asked time, new marks, expiry and privacy as text', async () => {
    fakeApi(approvalRoutes())
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'Songs for your class' })).toBeInTheDocument()
    expect(screen.getByText(/2 songs, 6:00 in total\. You asked for 5:00\./)).toBeInTheDocument()
    const rows = screen.getAllByTestId('song')
    expect(rows).toHaveLength(2)
    // A title is text, never markup.
    expect(rows[0]).toHaveTextContent('Hills <b>bold</b>')
    expect(rows[0]!.querySelector('b')).toBeNull()
    expect(rows[0]).toHaveTextContent('Climb')
    expect(rows[0]).toHaveTextContent('92 BPM')
    expect(rows[0]).toHaveTextContent('4:01')
    expect(rows[0]).not.toHaveTextContent('New')
    expect(rows[1]).toHaveTextContent('New')
    expect(screen.getByTestId('expiry')).toHaveTextContent(/Approve by .* these songs expire/)
    expect(screen.getByText(/anyone with its link can open it/)).toBeInTheDocument()
    expect(screen.getByText(/1 song was left out because MOWL has no BPM/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/spotify:|[A-Za-z0-9]{22}/)
  })

  it('links a song back to Spotify only with the API link, and drops one that is not a track link', async () => {
    const good = 'https://open.spotify.com/track/' + 'a'.repeat(22)
    const base = listBody().songs
    fakeApi(
      approvalRoutes(
        listBody({
          songs: [
            { ...base[0], open_spotify: good },
            { ...base[1], open_spotify: 'https://open.spotify.com/track/short' },
          ],
        }),
      ),
    )
    renderAt(`/jobs/${JOB_ID}`)
    const links = await screen.findAllByRole('link', { name: /^OPEN SPOTIFY, / })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', good)
    expect(links[0]).toHaveTextContent('OPEN SPOTIFY')
    expect(screen.getAllByTestId('song')[1]).not.toHaveTextContent('OPEN SPOTIFY')
  })

  it('approves with the revision id and the hash only, and a double click is one request', async () => {
    let approved = false
    const api = fakeApi({
      ...approvalRoutes(),
      [`GET ${JOB}`]: () =>
        approved ? jobBody('running', { kind: 'design_with_songs', approved_at: '2026-10-30T10:00:00Z' }) : waitingJob(),
      [`POST ${JOB}/approve`]: () => {
        approved = true
        return json(jobBody('queued', { kind: 'design_with_songs', approved_at: '2026-10-30T10:00:00Z' }))
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    const button = await screen.findByRole('button', { name: 'Approve songs' })
    // Both clicks land before React re-renders, so only the in-flight guard can stop the second.
    act(() => {
      button.click()
      button.click()
    })
    await waitFor(() => {
      expect(api.to('POST', `${JOB}/approve`)).toHaveLength(1)
    })
    const [call] = api.to('POST', `${JOB}/approve`)
    expect(call?.body).toEqual({ revision_id: LIST_ID, content_hash: HASH })
    // Byte-identical to the request before the restyle: the same two keys, in this order.
    expect(JSON.stringify(call?.body)).toBe(JSON.stringify({ revision_id: LIST_ID, content_hash: HASH }))
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    await waitFor(() => {
      expect(screen.getAllByRole('heading').map((h) => h.textContent)).toContain('Designing your class…')
    })
    expect(api.to('POST', `${JOB}/approve`)).toHaveLength(1)
  })

  const REFUSALS: [string, number, RegExp, boolean][] = [
    ['expired', 409, /These songs expired before they were approved/, true],
    ['superseded', 409, /A newer list of songs has replaced these/, true],
    ['hash_mismatch', 409, /These songs changed after this page was loaded/, true],
    ['account_changed', 409, /not the one these songs were chosen for/, true],
    ['invalid_input', 422, /approval was not accepted/, true],
    ['not_waiting', 409, /can no longer be approved/, true],
    ['not_connected', 409, /Spotify or MOWL is not connected/, false],
    ['busy', 429, /already have designs running/, false],
    ['quota', 409, /You have used your classes for this month/, false],
  ]
  for (const [code, status, text, final] of REFUSALS) {
    it(`says ${code} in plain words ${final ? 'and offers no second try' : 'and lets the user fix it'}`, async () => {
      const api = fakeApi({ ...approvalRoutes(), [`POST ${JOB}/approve`]: () => reason(status, code) })
      renderAt(`/jobs/${JOB_ID}`)
      fireEvent.click(await screen.findByRole('button', { name: 'Approve songs' }))
      expect(await screen.findByRole('alert')).toHaveTextContent(text)
      if (code.includes('_')) expect(document.body.textContent).not.toContain(code)
      if (final) {
        expect(screen.queryByRole('button', { name: 'Approve songs' })).toBeNull()
        expect(api.to('POST', `${JOB}/approve`)).toHaveLength(1)
      } else {
        fireEvent.click(screen.getByRole('button', { name: 'Approve songs' }))
        await waitFor(() => {
          expect(api.to('POST', `${JOB}/approve`)).toHaveLength(2)
        })
      }
    })
  }

  it('offers new songs after an expired or changed-account refusal, without approving again', async () => {
    const api = fakeApi({
      ...approvalRoutes(),
      [`POST ${JOB}/approve`]: () => reason(409, 'expired'),
      [`POST ${JOB}/cancel`]: jobBody('cancelled', { kind: 'design_with_songs' }),
      'GET /api/connections': connectionsBody(),
      [`GET ${CLASS_PATH}`]: classBody({ brief: BRIEF }),
      'POST /api/jobs': () => json(NEW_JOB, 201),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve songs' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Pick songs again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const [create] = api.to('POST', '/api/jobs')
    expect(create?.body).toMatchObject({
      kind: 'design_with_songs',
      input: { class_id: CLASS_ID, mode: 'pick', mowl_connection_id: MOWL_ID, spotify_connection_id: SPOTIFY_ID },
    })
    expect((create?.body as { input: Record<string, unknown> }).input.seed).toBeUndefined()
    expect(api.to('POST', `${JOB}/approve`)).toHaveLength(1)
  })

  it('shows a superseded list plainly, with no Approve button', async () => {
    fakeApi(approvalRoutes(listBody({ status: 'superseded' })))
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('A newer list replaced these songs')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve songs' })).toBeNull()
  })

  it('shows a list that is past its expiry as expired, with no Approve button', async () => {
    fakeApi(approvalRoutes(listBody({ status: 'expired' })))
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('These songs expired')).toBeInTheDocument()
    expect(screen.getByText(/Nothing was written to your Spotify/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve songs' })).toBeNull()
  })

  it('says when the songs could not be loaded and tries again on request', async () => {
    let tries = 0
    fakeApi({
      ...approvalRoutes(),
      [`GET ${LIST_PATH}`]: () => (++tries === 1 ? reason(500, 'internal') : listBody()),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('The songs could not be loaded. Nothing was approved.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: 'Approve songs' })).toBeInTheDocument()
  })
})

describe('Ask for changes', () => {
  const CHANGES = {
    ...approvalRoutes(),
    [`POST ${JOB}/cancel`]: jobBody('cancelled', { kind: 'design_with_songs' }),
    'GET /api/connections': connectionsBody(),
    [`GET ${CLASS_PATH}`]: classBody({ brief: BRIEF }),
  }

  it('needs a replace mark, then cancels the waiting job before it creates the seeded job', async () => {
    const order: string[] = []
    const api = fakeApi({
      ...CHANGES,
      [`POST ${JOB}/cancel`]: () => {
        order.push('cancel')
        return jobBody('cancelled', { kind: 'design_with_songs' })
      },
      'POST /api/jobs': () => {
        order.push('create')
        return json(NEW_JOB, 201)
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    const ask = await screen.findByRole('button', { name: 'Ask for changes' })
    expect(ask).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    fireEvent.change(screen.getByLabelText(/What should be different/), { target: { value: 'More rock' } })
    fireEvent.click(ask)
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    expect(order).toEqual(['cancel', 'create'])
    const [create] = api.to('POST', '/api/jobs')
    const input = (create?.body as { input: Record<string, unknown> }).input
    expect(input.seed).toEqual({ song_list_revision_id: LIST_ID, replace: [1] })
    expect(input.mode).toBe('pick')
    // The class's own brief, with the note appended.
    expect(input.brief).toEqual({ ...BRIEF, text: 'Hills\nMore rock' })
    expect(create?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('waits and tries again when the class is still busy with the cancelled job', async () => {
    let creates = 0
    const api = fakeApi({
      ...CHANGES,
      'POST /api/jobs': () => (++creates < 3 ? reason(409, 'class_busy') : json(NEW_JOB, 201)),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    fireEvent.click(screen.getByRole('button', { name: 'Ask for changes' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const keys = api.to('POST', '/api/jobs').map((c) => c.headers.get('Idempotency-Key'))
    expect(keys).toHaveLength(3)
    expect(new Set(keys).size).toBe(1)
  })

  it('says so when the class stays busy', async () => {
    fakeApi({ ...CHANGES, 'POST /api/jobs': () => reason(409, 'class_busy') })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    fireEvent.click(screen.getByRole('button', { name: 'Ask for changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/already has a task running/)
  })

  it('accepts a job that ended on its own (cancel answers 409)', async () => {
    fakeApi({
      ...CHANGES,
      [`POST ${JOB}/cancel`]: () => reason(409, 'not_cancellable'),
      'POST /api/jobs': () => json(NEW_JOB, 201),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    fireEvent.click(screen.getByRole('button', { name: 'Ask for changes' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
  })

  it('stops a revise_songs list and sends the user back to change the request', async () => {
    const api = fakeApi({
      ...CHANGES,
      [`GET ${JOB}`]: waitingJob({ kind: 'revise_songs' }),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('button', { name: 'Approve songs' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask for changes' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Replace/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Stop and change the request' })).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })
})

describe('the class page reaches the approval', () => {
  it('points at the waiting songs', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: { open_job: { id: JOB_ID, kind: 'design_with_songs', state: 'waiting_for_approval' } },
      }),
    })
    renderAt(`/classes/${CLASS_ID}`)
    expect(await screen.findByRole('link', { name: 'Review the songs' })).toHaveAttribute('href', `/jobs/${JOB_ID}`)
  })
})

describe('Ask for changes and the brief', () => {
  const base = {
    ...approvalRoutes(),
    [`POST ${JOB}/cancel`]: jobBody('cancelled', { kind: 'design_with_songs' }),
    'GET /api/connections': connectionsBody(),
    'POST /api/jobs': () => json(NEW_JOB, 201),
  }

  it('falls back to the form kept in this tab when the class has no brief', async () => {
    const kept = { ...BRIEF, text: 'From the tab', length_min: 60 }
    window.sessionStorage.setItem(
      `classbeat.pick-input.${JOB_ID}`,
      JSON.stringify({ class_id: CLASS_ID, brief: kept, mode: 'pick', mowl_connection_id: 'x', spotify_connection_id: 'y' }),
    )
    const api = fakeApi({ ...base, [`GET ${CLASS_PATH}`]: classBody({ brief: null }) })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    fireEvent.click(screen.getByRole('button', { name: 'Ask for changes' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const input = (api.to('POST', '/api/jobs')[0]?.body as { input: Record<string, unknown> }).input
    expect(input.brief).toEqual(kept)
  })

  it('rebuilds the length from the list when there is neither', async () => {
    const api = fakeApi({ ...base, [`GET ${CLASS_PATH}`]: classBody({ brief: null }) })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    fireEvent.click(screen.getByRole('button', { name: 'Ask for changes' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    const input = (api.to('POST', '/api/jobs')[0]?.body as { input: { brief: { length_min: number } } }).input
    expect(input.brief.length_min).toBe(10)
  })

  it('treats a malformed class brief as none', async () => {
    const api = fakeApi({ ...base, [`GET ${CLASS_PATH}`]: classBody({ brief: { length_min: 'long' } }) })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    fireEvent.click(screen.getByRole('button', { name: 'Ask for changes' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const input = (api.to('POST', '/api/jobs')[0]?.body as { input: { brief: { intensity: string } } }).input
    expect(input.brief.intensity).toBe('moderate')
  })

  it('a double click on Ask for changes is one cancel and one create', async () => {
    const api = fakeApi({ ...base, [`GET ${CLASS_PATH}`]: classBody({ brief: BRIEF }) })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Replace Rolling <i>hills</i>' }))
    const ask = screen.getByRole('button', { name: 'Ask for changes' })
    // Both clicks land before React re-renders, so only the in-flight guard can stop the second.
    act(() => {
      ask.click()
      ask.click()
    })
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEW_JOB.id}`)
    })
    expect(api.to('POST', `${JOB}/cancel`)).toHaveLength(1)
    expect(api.to('POST', '/api/jobs')).toHaveLength(1)
  })
})
