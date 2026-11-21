import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, classRoutes } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// #1342 (ADR 0036 amendment A8): a studio draft whose mix plays a song again is mix-only. The
// timeline marks the repeat and shows the soft hints by song number; the output choice shows the
// SwedeBeat playlist off and disabled with the reason, and the consent says the mix repeats songs.

const PAGE = `/classes/${CLASS_ID}`
const HASH = 'ab'.repeat(32)
const OUT_JOB = 'e2222222-2222-4222-8222-222222222222'

function piece(position: number, title: string, repeatOf: number | null) {
  return {
    position, title, artist: 'Band', bpm: 128, start_sec: 0, end_sec: 240, played_sec: 240,
    effective_sec: 232, mix_start_sec: position * 232, mix_end_sec: position * 232 + 240,
    crossfade_in_sec: position ? 8 : 0, crossfade_out_sec: position < 2 ? 8 : 0,
    repeat_of: repeatOf, loop: false,
  }
}

const REPEATED = {
  revision_id: REV1,
  content_hash: HASH,
  duration_sec: 704,
  workout_sec: 700,
  tracks: [piece(0, 'Alpha', null), piece(1, 'Beta', null), piece(2, 'Alpha', 0)],
  has_repeats: true,
  hints: [
    { code: 'whole_track_repeated', pieces: [0, 2] },
    { code: 'tempo_jump', pieces: [0, 1] },
    { code: 'downbeats_not_locked', pieces: [1, 2] },
    { code: 'not_a_hint_we_know', pieces: [1] },
  ],
  crossfades: [
    { after: 0, sec: 8, mix_from_sec: 232, mix_to_sec: 240 },
    { after: 1, sec: 8, mix_from_sec: 464, mix_to_sec: 472 },
  ],
  intervals: [{ segment_name: 'Climb', segment_kind: 'main', start_sec: 0, end_sec: 700, zone: 4, standing: false, track: 0 }],
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { studio: true } }),
    'GET /api/connections': { connections: [] },
    [`GET ${CLASS_PATH}/timeline`]: REPEATED,
    [`GET ${CLASS_PATH}/outputs`]: {
      enabled_outputs: ['spotify_playlist', 'swedebeat_playlist', 'swedebeat_mix', 'mowl'],
      outputs: [],
    },
    ...extra,
  }
}

describe('a studio class whose mix repeats a song', () => {
  it('marks the repeat and shows the known hints by song number', async () => {
    fakeApi(routes())
    renderAt(PAGE)
    expect(await screen.findAllByTestId('timeline-song')).toHaveLength(3)
    const marks = screen.getAllByTestId('timeline-repeat')
    expect(marks).toHaveLength(1)
    expect(marks[0]).toHaveTextContent('Plays song 1 again.')
    const hints = screen.getAllByTestId('timeline-hint')
    expect(hints).toHaveLength(3) // an unknown code is never shown
    expect(hints[0]).toHaveTextContent('Songs 1, 3 play the same whole track')
    expect(hints[1]).toHaveTextContent('Songs 1, 2 are at tempos that clash')
    expect(hints[2]).toHaveTextContent('crossfade between songs 2, 3 does not line up')
  })

  it('offers the SwedeBeat playlist off and disabled, with the reason, and says so in the consent', async () => {
    fakeApi(routes())
    renderAt(PAGE)
    const playlist = await screen.findByRole('checkbox', { name: /SwedeBeat playlist/ })
    expect(playlist).not.toBeChecked()
    expect(playlist).toBeDisabled()
    expect(screen.getByTestId('studio-out-mix-only')).toHaveTextContent(
      'This class repeats a song, so it can be produced as a mix only.',
    )
    expect(screen.getByRole('checkbox', { name: /SwedeBeat mix/ })).toBeEnabled()
    expect(screen.getByTestId('studio-consent')).toHaveTextContent('more than once')
  })

  it('never sends the SwedeBeat playlist for it', async () => {
    const api = fakeApi(
      routes({
        [`POST ${CLASS_PATH}/outputs`]: () => json({ id: OUT_JOB, kind: 'produce_outputs', state: 'queued' }, 201),
      }),
    )
    renderAt(PAGE)
    await screen.findByTestId('studio-consent')
    fireEvent.click(screen.getByRole('checkbox', { name: /SwedeBeat mix/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Approve and create' }))
    await waitFor(() => { expect(api.to('POST', `${CLASS_PATH}/outputs`)).toHaveLength(1) })
    expect(api.to('POST', `${CLASS_PATH}/outputs`)[0]!.body).toEqual({
      revision_id: REV1,
      content_hash: HASH,
      outputs: ['spotify_playlist', 'swedebeat_mix'],
    })
  })

  it('words the server refusal in plain text, never its code', async () => {
    fakeApi(routes({ [`POST ${CLASS_PATH}/outputs`]: () => reason(409, 'playlist_has_repeats') }))
    renderAt(PAGE)
    await screen.findByTestId('studio-consent')
    fireEvent.click(screen.getByRole('button', { name: 'Approve and create' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Choose the mix instead of the SwedeBeat playlist.')
    expect(alert).not.toHaveTextContent('playlist_has_repeats')
  })
})
