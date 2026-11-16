import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, REVISE_JOB, classRoutes } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody } from '../test/jobFixtures'
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
    ...classRoutes(),
    'GET /api/connections': SWEDEBEAT,
    [`GET ${CLASS_PATH}/timeline`]: TIMELINE,
    ...extra,
  }
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
    const mix = screen.getByRole('checkbox', { name: /SwedeBeat mix/ })
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
    fireEvent.click(screen.getByRole('checkbox', { name: /SwedeBeat mix/ }))
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
    fireEvent.click(screen.getByRole('button', { name: 'Approve and create' }))
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
    fireEvent.click(screen.getByRole('button', { name: 'Approve and create' }))
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

  it('shows nothing of the studio on a class that is not one', async () => {
    fakeApi({ ...SESSION_ROUTES, ...classRoutes() })
    renderAt(PAGE)
    await screen.findByTestId('tss')
    expect(screen.queryByTestId('studio-timeline')).toBeNull()
    expect(screen.queryByTestId('studio-outputs')).toBeNull()
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
})
