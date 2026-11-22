import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, REVISE_JOB, classBody, classRoutes } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
import { DRAFT_MIX, PREVIEW_PATH, previewBody } from '../test/mixFixtures'
import { renderAt } from '../test/render'

const PAGE = `/classes/${CLASS_ID}`
const HASH = 'cd'.repeat(32)
const SB_CONNECTION = 'd1111111-1111-4111-8111-111111111111'
const OUT_JOB = 'e1111111-1111-4111-8111-111111111111'

const SWEDEBEAT = {
  connections: [
    {
      id: SB_CONNECTION,
      provider: 'swedebeat',
      status: 'connected',
      last_error: null,
      connected_at: '2026-10-01T10:00:00Z',
      last_used_at: null,
    },
  ],
}

const TIMELINE = {
  revision_id: REV1,
  content_hash: HASH,
  ...DRAFT_MIX,
  duration_sec: 1320,
  workout_sec: 1200,
  tracks: [
    {
      position: 0, title: 'First <b>song</b>', artist: 'Band', bpm: 127.6,
      start_sec: 5, end_sec: 245, played_sec: 240, effective_sec: 232,
      mix_start_sec: 0, mix_end_sec: 232, crossfade_in_sec: 0, crossfade_out_sec: 8,
    },
    {
      position: 1, title: 'Second song', artist: 'Duo', bpm: 130,
      start_sec: 0, end_sec: 300, played_sec: 300, effective_sec: 292,
      mix_start_sec: 224, mix_end_sec: 516, crossfade_in_sec: 8, crossfade_out_sec: 0,
    },
  ],
  crossfades: [{ after: 0, sec: 8, mix_from_sec: 224, mix_to_sec: 232 }],
  intervals: [
    { segment_name: 'Climb', segment_kind: 'main', start_sec: 0, end_sec: 120, zone: 4, standing: true, track: 0 },
    { segment_name: 'Easy', segment_kind: 'recovery', start_sec: 120, end_sec: 180, zone: null, standing: false, track: 1 },
  ],
}

function studioRoutes(extra: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { studio: true } }),
    'GET /api/connections': SWEDEBEAT,
    [`GET ${CLASS_PATH}/timeline`]: TIMELINE,
    [`POST ${PREVIEW_PATH}`]: previewBody(),
    [`GET ${CLASS_PATH}/outputs`]: {
      enabled_outputs: ['spotify_playlist', 'swedebeat_playlist', 'swedebeat_mix', 'mowl'],
      outputs: [],
    },
    ...extra,
  }
}

async function approveButton() {
  const button = await screen.findByRole('button', { name: 'Approve and create' })
  await waitFor(() => { expect(button).toBeEnabled() })
  return button
}

describe('a studio class on the class page', () => {
  it('shows the timeline with songs, cuts, crossfades and intervals, and nothing to play', async () => {
    fakeApi(studioRoutes())
    const { container } = renderAt(PAGE)
    expect(await screen.findAllByTestId('timeline-song')).toHaveLength(2)
    expect(screen.getByText(/First <b>song<\/b>/)).toBeInTheDocument()
    expect(screen.getAllByTestId('timeline-fade')).toHaveLength(1)
    expect(screen.getAllByTestId('timeline-interval')).toHaveLength(2)
    expect(container.querySelector('audio, video')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
  })

  it('offers the defaults of the matrix and keeps MOWL tied to the mix', async () => {
    fakeApi(studioRoutes())
    renderAt(PAGE)
    const spotify = await screen.findByRole('checkbox', { name: /Spotify playlist/ })
    expect(spotify).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /SwedeBeat playlist/ })).toBeChecked()
    const mix = screen.getByRole('checkbox', { name: /^DJ mix/ })
    const mowl = screen.getByRole('checkbox', { name: /MOWL/ })
    expect(mix).not.toBeChecked()
    expect(mowl).not.toBeChecked()
    fireEvent.click(mowl)
    expect(mix).toBeChecked()
    expect(mix).toBeDisabled()
    fireEvent.click(mowl)
    expect(mix).toBeEnabled()
  })

  it('tells what a choice means before it is approved', async () => {
    fakeApi(studioRoutes())
    renderAt(PAGE)
    await screen.findByTestId('studio-consent')
    const consent = screen.getByTestId('studio-consent')
    expect(consent).toHaveTextContent('SwedeBeat has no delete')
    expect(consent).toHaveTextContent('name and email')
    expect(consent).not.toHaveTextContent('merged mix')
    fireEvent.click(screen.getByRole('checkbox', { name: /^DJ mix/ }))
    expect(screen.getByTestId('studio-consent')).toHaveTextContent('merged mix')
  })

  it('sends the draft, its hash and the chosen outputs with a key, then follows the job', async () => {
    const api = fakeApi(
      studioRoutes({
        [`POST ${CLASS_PATH}/outputs`]: () => json({ id: OUT_JOB, kind: 'produce_outputs', state: 'queued' }, 201),
      }),
    )
    renderAt(PAGE)
    await screen.findByTestId('studio-consent')
    fireEvent.click(await approveButton())
    await waitFor(() => { expect(api.to('POST', `${CLASS_PATH}/outputs`)).toHaveLength(1) })
    const call = api.to('POST', `${CLASS_PATH}/outputs`)[0]!
    expect(call.body).toEqual({
      revision_id: REV1,
      content_hash: HASH,
      outputs: ['spotify_playlist', 'swedebeat_playlist'],
    })
    expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it.each(['already_designed', 'hash_mismatch', 'stale_base'])('words %s in plain text', async (why) => {
    fakeApi(studioRoutes({ [`POST ${CLASS_PATH}/outputs`]: () => reason(409, why) }))
    renderAt(PAGE)
    await screen.findByTestId('studio-consent')
    fireEvent.click(await approveButton())
    const alert = await screen.findByRole('alert')
    expect(alert).not.toHaveTextContent(why)
    expect(alert.textContent?.length).toBeGreaterThan(10)
  })

  it('says the music needs a new analysis and offers Change songs', async () => {
    fakeApi(studioRoutes({ [`GET ${CLASS_PATH}/timeline`]: () => reason(409, 'music_changed') }))
    renderAt(PAGE)
    expect(await screen.findByTestId('studio-music-changed')).toHaveTextContent('The music needs a new analysis')
    expect(screen.getByRole('link', { name: 'Change songs' })).toBeInTheDocument()
    expect(screen.queryByTestId('studio-outputs')).toBeNull()
  })

  it('shows no legacy song panel on a studio class', async () => {
    fakeApi(studioRoutes())
    renderAt(PAGE)
    await screen.findAllByTestId('timeline-song')
    expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
    expect(screen.queryByText('The songs could not be loaded. Reload the page.')).toBeNull()
  })

  it('shows no legacy song panel when the studio music needs a new analysis', async () => {
    fakeApi(studioRoutes({ [`GET ${CLASS_PATH}/timeline`]: () => reason(409, 'music_changed') }))
    renderAt(PAGE)
    await screen.findByTestId('studio-music-changed')
    expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
  })

  it('shows nothing of the studio on a class that is not one, and never asks for its timeline', async () => {
    const api = fakeApi({ ...SESSION_ROUTES, ...classRoutes() })
    renderAt(PAGE)
    await screen.findByTestId('tss')
    expect(screen.queryByTestId('studio-timeline')).toBeNull()
    expect(screen.queryByTestId('studio-outputs')).toBeNull()
    expect(api.to('GET', `${CLASS_PATH}/timeline`)).toHaveLength(0)
  })

  it('asks for the timeline once of a class the summary says is a studio class', async () => {
    const api = fakeApi(studioRoutes())
    renderAt(PAGE)
    await screen.findAllByTestId('timeline-song')
    expect(api.to('GET', `${CLASS_PATH}/timeline`)).toHaveLength(1)
  })

  it('sends a chat change as studio_revise on the SwedeBeat connection, though Change it is off', async () => {
    const api = fakeApi(
      studioRoutes({
        [`GET ${CLASS_PATH}/history`]: { sport: 'cycling', can_change: false, revisions: [], unreadable: 0 },
        'POST /api/jobs': () => jobBody('queued', { id: REVISE_JOB, kind: 'studio_revise' }),
      }),
    )
    renderAt(PAGE)
    await screen.findByTestId('studio-consent')
    const box = await screen.findByLabelText('What should change?')
    await waitFor(() => { expect(box).toBeEnabled() })
    fireEvent.change(box, { target: { value: 'Swap the second song' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change it' }))
    await waitFor(() => { expect(api.to('POST', '/api/jobs')).toHaveLength(1) })
    expect(api.to('POST', '/api/jobs')[0]!.body).toEqual({
      kind: 'studio_revise',
      input: {
        class_id: CLASS_ID,
        base_revision_id: REV1,
        request: 'Swap the second song',
        swedebeat_connection_id: SB_CONNECTION,
      },
    })
  })

  describe('an approved list whose design never started', () => {
    const SONGS_JOB = 'f1111111-1111-4111-8111-111111111111'
    const LIST = '55555555-5555-4555-8555-555555555555'
    const DESIGN_JOB = '77777777-7777-4777-8777-777777777777'
    const approved = (kind: string, extra: Record<string, unknown> = {}) => ({
      ...SESSION_ROUTES,
      ...classRoutes({
        cls: { songs: { state: 'approved', job_id: SONGS_JOB, revision_id: LIST, content_hash: HASH } },
      }),
      'GET /api/connections': SWEDEBEAT,
      // No `studio` flag: the stored music is set by the first studio design, so the timeline is not asked
      // for and the kind is found by the approved list's job.
      [`GET /api/jobs/${SONGS_JOB}`]: jobBody('completed', { id: SONGS_JOB, kind }),
      ...extra,
    })

    it('after a reload offers Start the design, which posts the start with a key', async () => {
      const api = fakeApi(
        approved('studio_songs', {
          [`POST ${CLASS_PATH}/studio-songs/${LIST}/design`]: () =>
            json({ id: DESIGN_JOB, kind: 'studio_design', state: 'queued' }, 201),
        }),
      )
      renderAt(PAGE)
      fireEvent.click(await screen.findByRole('button', { name: 'Start the design' }))
      await waitFor(() => { expect(api.to('POST', `${CLASS_PATH}/studio-songs/${LIST}/design`)).toHaveLength(1) })
      const call = api.to('POST', `${CLASS_PATH}/studio-songs/${LIST}/design`)[0]!
      expect(call.body).toEqual({})
      expect(call.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    })

    it('words a design that ran and failed as failed, not as never started', async () => {
      window.sessionStorage.setItem(`classbeat.studio-design.${CLASS_ID}`, DESIGN_JOB)
      fakeApi(approved('studio_songs', { [`GET /api/jobs/${DESIGN_JOB}`]: jobBody('failed', { id: DESIGN_JOB, kind: 'studio_design' }) }))
      renderAt(PAGE)
      expect(await screen.findByText('The songs are approved, but the design failed.')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Start the design' })).toBeInTheDocument()
      window.sessionStorage.clear()
    })

    it('shows no legacy song panel for it', async () => {
      fakeApi(approved('studio_songs'))
      renderAt(PAGE)
      await screen.findByRole('button', { name: 'Start the design' })
      expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
    })

    it('keeps the legacy song panel on a Spotify or SwedeBeat-mix class (approved songs are not a studio list)', async () => {
      fakeApi(approved('revise_songs'))
      renderAt(PAGE)
      expect(await screen.findByRole('region', { name: 'Change songs' })).toBeInTheDocument()
    })

    it('asks for no legacy song list and shows no error while the studio kind is still being found', async () => {
      let release: () => void = () => undefined
      const gate = new Promise<void>((resolve) => {
        release = resolve
      })
      const SONGS_PATH = `${CLASS_PATH}/revisions/${LIST}`
      const api = fakeApi(
        approved('studio_songs', {
          [`GET /api/jobs/${SONGS_JOB}`]: () => gate.then(() => jobBody('completed', { id: SONGS_JOB, kind: 'studio_songs' })),
          [`GET ${SONGS_PATH}`]: () => reason(500, 'boom'),
        }),
      )
      renderAt(PAGE)
      await screen.findByTestId('tss')
      expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
      expect(screen.queryByRole('link', { name: 'Change songs' })).toBeNull()
      release()
      await screen.findByRole('button', { name: 'Start the design' })
      expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
      expect(screen.queryByText('The songs could not be loaded. Reload the page.')).toBeNull()
      expect(api.to('GET', SONGS_PATH)).toHaveLength(0)
      expect(api.to('GET', `/api/jobs/${SONGS_JOB}`)).toHaveLength(1)
    })

    const OPEN = { open_job_id: REVISE_JOB, open_job: { id: REVISE_JOB, kind: 'revise_draft', state: 'running' } }
    const withOpenJob = (kind: string, extra: Record<string, unknown> = {}) => {
      const routes = approved(kind, extra)
      return {
        ...routes,
        [`GET ${CLASS_PATH}`]: classBody({ songs: { state: 'approved', job_id: SONGS_JOB, revision_id: LIST, content_hash: HASH }, ...OPEN }),
      }
    }

    it('still shows the legacy song panel, waiting, on a Spotify class loaded with a job open', async () => {
      fakeApi(withOpenJob('design_with_songs'))
      renderAt(PAGE)
      const panel = await screen.findByRole('region', { name: 'Change songs' })
      expect(panel).toHaveTextContent(/wait/i)
    })

    it('never shows the legacy song panel on a studio class loaded with a job open', async () => {
      const api = fakeApi(withOpenJob('studio_songs'))
      renderAt(PAGE)
      await screen.findByTestId('tss')
      await waitFor(() => { expect(api.to('GET', `/api/jobs/${SONGS_JOB}`)).toHaveLength(1) })
      expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
      expect(api.to('GET', `${CLASS_PATH}/revisions/${LIST}`)).toHaveLength(0)
    })

    it('keeps the legacy song panel on a non-studio class without asking for its timeline', async () => {
      const api = fakeApi(approved('design_with_songs'))
      renderAt(PAGE)
      expect(await screen.findByRole('region', { name: 'Change songs' })).toBeInTheDocument()
      expect(api.to('GET', `${CLASS_PATH}/timeline`)).toHaveLength(0)
    })

    it('offers nothing for a class whose approved songs are not a studio list', async () => {
      fakeApi(approved('design_with_songs'))
      renderAt(PAGE)
      await screen.findByTestId('tss')
      expect(await screen.findByRole('region', { name: 'Change songs' })).toBeInTheDocument()
      await waitFor(() => { expect(screen.queryByTestId('studio-music-changed')).toBeNull() })
      expect(screen.queryByRole('button', { name: 'Start the design' })).toBeNull()
    })
  })

  it('offers only the enabled outputs and falls the defaults back to them', async () => {
    fakeApi(
      studioRoutes({
        [`GET ${CLASS_PATH}/outputs`]: { enabled_outputs: ['swedebeat_playlist', 'swedebeat_mix'], outputs: [] },
      }),
    )
    renderAt(PAGE)
    expect(await screen.findByRole('checkbox', { name: /SwedeBeat playlist/ })).toBeChecked()
    expect(screen.queryByRole('checkbox', { name: /Spotify playlist/ })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: /MOWL/ })).toBeNull()
    expect(screen.getByRole('checkbox', { name: /^DJ mix/ })).not.toBeChecked()
  })

  it('says so when no output is enabled', async () => {
    fakeApi(studioRoutes({ [`GET ${CLASS_PATH}/outputs`]: { enabled_outputs: [], outputs: [] } }))
    renderAt(PAGE)
    expect(await screen.findByText(/No output is available/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve and create' })).toBeDisabled()
  })

  it('shows a running output state from the read of the outputs', async () => {
    fakeApi(
      studioRoutes({
        [`GET ${CLASS_PATH}/outputs`]: {
          enabled_outputs: ['spotify_playlist', 'swedebeat_playlist'],
          outputs: [
            { revision_id: REV1, output: 'swedebeat_playlist', attempt: 1, state: 'running', actions: [], note: null },
          ],
        },
      }),
    )
    renderAt(PAGE)
    expect(await screen.findByTestId('studio-output-state-swedebeat_playlist')).toHaveTextContent('Being created')
  })

  it('shows the output-limit notice after a stopped approval but leaves Approve enabled', async () => {
    window.sessionStorage.setItem('classbeat.studio-output-limit.' + CLASS_ID, '1')
    fakeApi(studioRoutes())
    renderAt(PAGE)
    expect(await screen.findByText(/24 per day across all your classes/)).toBeInTheDocument()
    await waitFor(() => { expect(screen.getByRole('button', { name: 'Approve and create' })).toBeEnabled() })
    window.sessionStorage.clear()
  })
})
