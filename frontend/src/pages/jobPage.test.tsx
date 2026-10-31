import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fakeApi, json, reason } from '../test/fakeApi'
import {
  CLASS_ID,
  DRAFT,
  JOB_ID,
  OPERATION_ID,
  SESSION_ROUTES,
  jobBody,
  openStream,
  sse,
} from '../test/jobFixtures'
import { renderAt } from '../test/render'

const JOB = `/api/jobs/${JOB_ID}`
const EVENTS = `${JOB}/events`
const OPS = `GET /api/external-operations?job_id=${JOB_ID}`

describe('the Designing page', () => {
  it('shows progress from the events and resumes after a dropped stream with Last-Event-ID', async () => {
    let streams = 0
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('running'),
      [`GET ${EVENTS}`]: () => {
        streams += 1
        return streams === 1
          ? sse(
              [1, 'queued'],
              [2, 'claimed', { attempt: 1 }],
              [3, 'stage_started', { stage: 'music' }],
              [4, 'external_operation', { kind: 'mowl_attach_spotify_playlist', state: 'pending' }],
            )
          : openStream()
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'Designing your class…' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('progress')).toHaveTextContent('Reading your playlist')
    })
    expect(screen.getByTestId('progress')).toHaveTextContent('Adding your playlist to your MOWL music.')
    await waitFor(() => {
      expect(api.to('GET', EVENTS)).toHaveLength(2)
    })
    const [first, second] = api.to('GET', EVENTS)
    expect(first?.headers.get('Last-Event-ID')).toBeNull()
    expect(second?.headers.get('Last-Event-ID')).toBe('4')
  })

  it.each([
    ['pool_search', 'Finding songs that fit', 1],
    ['pool_write', 'Saving your working playlist', 2],
    ['pool_analyse', 'MOWL is listening to the music', 3],
    ['choose', 'Choosing the best songs for each part', 4],
  ])('words the phase-1 stage %s and counts the step', async (stage, label, step) => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('running', { kind: 'design_with_songs' }),
      [`GET ${EVENTS}`]: (() => {
        let opened = 0
        return () => (++opened > 1 ? openStream() : sse([1, 'queued'], [2, 'stage_started', { stage }]))
      })(),
    })
    renderAt(`/jobs/${JOB_ID}`)
    await waitFor(() => {
      expect(screen.getByTestId('progress')).toHaveTextContent(label)
    })
    expect(screen.getByText(`Step ${String(step)} of 4`)).toBeInTheDocument()
  })

  it('shows no internal word and a ghost profile that pulses only without reduced motion', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('running'),
      [`GET ${EVENTS}`]: () => openStream(),
    })
    renderAt(`/jobs/${JOB_ID}`)
    await screen.findByRole('button', { name: 'Stop designing' })
    expect(document.body.textContent).not.toMatch(/\b(jobs?|stages?|credits?|revisions?|tokens?)\b/i)
    expect(screen.getByText(/You can close this page/)).toBeInTheDocument()
    expect(document.querySelector('svg[class~="motion-safe:animate-pulse"]')).not.toBeNull()
  })

  it('shows a scheduled retry in plain words and the designing stage', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('queued'),
      [`GET ${EVENTS}`]: (() => {
        let opened = 0
        return () => {
          opened += 1
          return opened > 1
            ? openStream()
            : sse([1, 'queued'], [2, 'stage_started', { stage: 'design' }], [3, 'tool_called', { tool: 'preview_draft', ok: true }])
        }
      })(),
    })
    renderAt(`/jobs/${JOB_ID}`)
    await waitFor(() => {
      expect(screen.getByTestId('progress')).toHaveTextContent('Building your class')
    })
    expect(screen.getByTestId('progress')).toHaveTextContent('checked its draft 1 time')
  })

  it('does not reconnect after a terminal event and then shows the read-only draft as text', async () => {
    let reads = 0
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: () => {
        reads += 1
        return reads === 1 ? jobBody('running') : jobBody('completed')
      },
      [`GET ${EVENTS}`]: () => sse([1, 'queued'], [2, 'completed', { revision_id: DRAFT.revision_id }]),
      [`GET ${JOB}/revision`]: DRAFT,
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'Your class is ready' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the class' })).toHaveAttribute('href', `/classes/${CLASS_ID}`)
    // Server text is text: markup in names, notes and titles is shown literally, never parsed.
    expect(await screen.findByText('Tuesday climb <b>bold</b>')).toBeInTheDocument()
    expect(screen.getByText('Two hills <img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(screen.getByText('Song <i>without</i> BPM')).toBeInTheDocument()
    expect(document.querySelector('img, b, i')).toBeNull()
    expect(screen.getByText('5:00')).toBeInTheDocument()
    expect(screen.getByText('on the beat')).toBeInTheDocument()
    expect(screen.getByText('80 to 90 rpm')).toBeInTheDocument()
    expect(screen.getByText(/6:30 in total/)).toBeInTheDocument()
    expect(screen.getByTestId('unknown-bpm')).toHaveTextContent('guess')
    expect(api.to('GET', EVENTS)).toHaveLength(1) // #116: no second stream after the end
    expect(api.to('GET', `${JOB}/revision`)).toHaveLength(1)
  })

  it('reads a finished job without opening a stream', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('completed'),
      [`GET ${JOB}/revision`]: reason(409, 'no_revision'),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText('This design finished without a draft.')).toBeInTheDocument()
    expect(api.to('GET', EVENTS)).toHaveLength(0)
  })

  it('stops the job when asked and says it is stopping', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('running'),
      [`GET ${EVENTS}`]: openStream,
      [`POST ${JOB}/cancel`]: jobBody('running', { cancel_requested: true }),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Stop designing' }))
    await waitFor(() => {
      expect(screen.getByTestId('progress')).toHaveTextContent('Stopping.')
    })
    const [call] = api.to('POST', `${JOB}/cancel`)
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(screen.getByRole('button', { name: 'Stop designing' })).toBeDisabled()
  })

  it('answers an unknown or foreign job like any missing page', async () => {
    fakeApi({ ...SESSION_ROUTES, [`GET ${JOB}`]: reason(404, 'not_found') })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'Class not found' })).toBeInTheDocument()
  })

  it('keeps trying to read the job while the server is unreachable', async () => {
    let reads = 0
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: () => {
        reads += 1
        return reads === 1 ? json({}, 502) : jobBody('cancelled')
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText(/could not be reached/)).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'You stopped this design' }, { timeout: 3000 })).toBeInTheDocument()
  })
})

describe('a failed design', () => {
  it('says what happened in plain words, never the category code', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('failed', { error_category: 'upstream_unavailable' }),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('heading', { name: 'MOWL could not be reached' })).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('upstream_unavailable')
  })

  it('points to the account page when MOWL has to be connected again', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('failed', { error_category: 'needs_reconnect' }),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('link', { name: 'Go to your account' })).toHaveAttribute('href', '/account')
  })

  it.each(['cancelled', 'expired'])('offers "Design again", not "Try again", for a %s design', async (state) => {
    window.sessionStorage.setItem(
      `classbeat.design-input.${JOB_ID}`,
      JSON.stringify({
        title: 'Hills',
        brief: { length_min: 45, intensity: 'moderate', climbs: 1, sprints: 0, races: 0, ftp_test: null, style: '', text: '' },
        music: { source: 'mowl_spotify_playlist', spotify_playlist_id: 'AbCdEfGhIjKlMnOpQrStUv' },
        mowl_connection_id: 'c0000000-0000-4000-8000-000000000001',
      }),
    )
    fakeApi({ ...SESSION_ROUTES, [`GET ${JOB}`]: jobBody(state) })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('button', { name: 'Design again' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it.each([
    ['design_with_songs', 'design', 6, 6],
    ['design_with_songs', 'music', 5, 6],
    ['revise_songs', 'choose', 4, 10],
    ['revise_songs', 'revise', 10, 10],
  ])('counts %s at %s as step %i of %i', async (kind, stage, step, total) => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('running', { kind, approved_at: kind === 'design_with_songs' ? '2026-10-29T11:00:00Z' : null }),
      [`GET ${EVENTS}`]: (() => {
        let opened = 0
        return () => (++opened > 1 ? openStream() : sse([1, 'queued'], [2, 'stage_started', { stage }]))
      })(),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText(`Step ${String(step)} of ${String(total)}`)).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(total)
    // `revise_songs` never runs the stage "design", so it has no such step.
    const labels = screen.getAllByRole('listitem').map((li) => li.textContent ?? '')
    expect(labels.some((l) => l.startsWith('Building your class'))).toBe(kind !== 'revise_songs')
  })

  it('starts the same design again as a new job of the same class', async () => {
    const input = {
      title: 'Hills',
      brief: { length_min: 45, intensity: 'moderate', climbs: 1, sprints: 0, races: 0, ftp_test: null, style: '', text: '' },
      music: { source: 'mowl_spotify_playlist', spotify_playlist_id: 'AbCdEfGhIjKlMnOpQrStUv' },
      mowl_connection_id: 'c0000000-0000-4000-8000-000000000001',
    }
    window.sessionStorage.setItem(`classbeat.design-input.${JOB_ID}`, JSON.stringify(input))
    const NEXT = '55555555-5555-4555-8555-555555555555'
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('failed', { error_category: 'model_unavailable' }),
      'POST /api/jobs': () => json({ ...jobBody('queued'), id: NEXT }, 201),
      [`GET /api/jobs/${NEXT}`]: { ...jobBody('completed'), id: NEXT },
      [`GET /api/jobs/${NEXT}/revision`]: reason(409, 'no_revision'),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/jobs/${NEXT}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    expect(call?.body).toEqual({
      kind: 'design_draft',
      input: {
        class_id: CLASS_ID,
        brief: input.brief,
        music: input.music,
        mowl_connection_id: input.mowl_connection_id,
      },
    })
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    window.sessionStorage.clear()
  })

  it('offers the form instead when the earlier input is not kept in this tab', async () => {
    window.sessionStorage.clear()
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody('cancelled'),
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Start a new design' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/classes/new')
    })
  })
})

describe('an uncertain playlist attach', () => {
  const failed = jobBody('failed', { error_category: 'uncertain_write' })
  const uncertain = { id: OPERATION_ID, kind: 'mowl_attach_spotify_playlist', state: 'uncertain', job_id: JOB_ID, resolved_by: null }
  const listed = (operations: unknown[]) => ({ operations })

  it('explains it and offers Check again and Do it anyway, nothing else yet', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OPS]: listed([uncertain]),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Do it anyway' })).toBeInTheDocument()
    expect(screen.getByText(/may or may not be there now/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Design again' })).toBeNull()
  })

  it('Check again that finds the playlist lets the user design again', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OPS]: listed([uncertain]),
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: { ...uncertain, state: 'succeeded' },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(/playlist is in your MOWL music/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Start a new design|Design again/ })).toBeInTheDocument()
    expect(api.to('POST', `/api/external-operations/${OPERATION_ID}/check-again`)[0]?.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })

  it('Check again that stays unsure keeps both actions', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OPS]: listed([uncertain]),
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: uncertain,
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(/still cannot tell/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Do it anyway' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Design again|Start a new design/ })).toBeNull()
  })

  it('Do it anyway asks first and warns about a second copy', async () => {
    const api = fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OPS]: listed([uncertain]),
      [`POST /api/external-operations/${OPERATION_ID}/do-it-anyway`]: {
        ...uncertain,
        state: 'failed',
        resolved_by: 'user',
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Do it anyway' }))
    expect(screen.getByText(/two copies of it/)).toBeInTheDocument()
    expect(api.to('POST', `/api/external-operations/${OPERATION_ID}/do-it-anyway`)).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, add it again' }))
    expect(await screen.findByText(/may leave two copies there/)).toBeInTheDocument()
    expect(api.to('POST', `/api/external-operations/${OPERATION_ID}/do-it-anyway`)).toHaveLength(1)
    expect(screen.getByRole('button', { name: /Start a new design|Design again/ })).toBeInTheDocument()
  })

  it('a final or settled row (409) refreshes the list instead of failing the page', async () => {
    let settled = false
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OPS]: () => {
        return listed(settled ? [] : [uncertain])
      },
      [`POST /api/external-operations/${OPERATION_ID}/check-again`]: () => {
        settled = true
        return reason(409, 'final')
      },
    })
    renderAt(`/jobs/${JOB_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText('That was already settled. The list below is up to date.')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull()
    })
  })

  it('with no undecided operation listed (a hidden stale row) it says so and allows a new design', async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: failed,
      [OPS]: listed([]),
      'GET /api/external-operations': listed([]),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByTestId('no-undecided')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull()
    expect(await screen.findByRole('button', { name: /Start a new design|Design again/ })).toBeInTheDocument()
  })
})
