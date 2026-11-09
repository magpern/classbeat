import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { designSteps, failureText, reviseFailureText } from '../api/jobMessages'
import { MOWL_CONNECTION, classRoutes, message, REV1 } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, JOB_ID, PLAYLIST_ID, SESSION_ROUTES, jobBody, openStream } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// SL6 (#532, ADR 0007 amendment of 2026-11-03, item 10): "No music" on New class, the class page,
// the chat panel and the job page. The API is faked; nothing here reaches a network.

const REVISE_JOB = 'b1111111-1111-4111-8111-111111111111'
const MOWL = {
  id: MOWL_CONNECTION,
  provider: 'mowl',
  status: 'connected',
  last_error: null,
  connected_at: '2026-10-28T10:00:00Z',
  last_used_at: null,
  expires_at: null,
}

function newClassRoutes(connections: Record<string, unknown>, playlists = [{ spotify_playlist_id: PLAYLIST_ID, name: 'Tempo' }]) {
  return {
    ...SESSION_ROUTES,
    'GET /api/connections': { connections: [MOWL], ...connections },
    'GET /api/music/mowl-playlists': { playlists },
    'POST /api/jobs': () => json(jobBody('queued'), 201),
    [`GET /api/jobs/${JOB_ID}`]: jobBody('running'),
    [`GET /api/jobs/${JOB_ID}/events`]: openStream,
  }
}

afterEach(() => {
  window.sessionStorage.clear()
})

describe('the new-class form and No music', () => {
  it('offers no such choice, and no Music group, while the effective flag is off or absent', async () => {
    for (const connections of [{}, { soundless_enabled: false }]) {
      fakeApi(newClassRoutes(connections))
      const view = renderAt('/classes/new')
      await screen.findByLabelText('Class name')
      expect(screen.queryByRole('radio', { name: 'No music' })).toBeNull()
      expect(screen.queryByText('Music', { selector: 'span' })).toBeNull()
      expect(document.body).not.toHaveTextContent(/no music/i)
      view.unmount()
    }
  })

  it('offers it, not chosen, with the playlist choice still the default', async () => {
    fakeApi(newClassRoutes({ soundless_enabled: true }))
    renderAt('/classes/new')
    await screen.findByLabelText('Class name')
    expect(screen.getByRole('radio', { name: 'No music' })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: 'My playlist' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Tempo' })).toBeInTheDocument()
  })

  it('sends music {source: none}, needs no playlist and hides the playlist, song and mix inputs', async () => {
    const api = fakeApi(
      newClassRoutes({
        soundless_enabled: true,
        swedebeat: { enabled: true, available: true, uses_swedebeat: true },
      }),
    )
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Silent ride' } })
    fireEvent.click(screen.getByRole('radio', { name: 'No music' }))
    expect(screen.getByRole('radio', { name: 'No music' })).toBeChecked()
    expect(screen.getByText(/play your own music/)).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'Tempo' })).toBeNull()
    expect(screen.queryByText(/classbeat adds this playlist/)).toBeNull()
    // The summary names the choice.
    expect(screen.getAllByText('No music').length).toBeGreaterThan(1)
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/en/jobs/${JOB_ID}`)
    })
    const [call] = api.to('POST', '/api/jobs')
    const input = (call?.body as { input: { music: unknown; mowl_connection_id: string } }).input
    expect(input.music).toEqual({ source: 'none' })
    expect(JSON.stringify(call?.body)).not.toMatch(/spotify_playlist_id|mix_id/)
    expect(input.mowl_connection_id).toBe(MOWL_CONNECTION)
  })

  it('still asks for a playlist when another choice is picked again', async () => {
    const api = fakeApi(newClassRoutes({ soundless_enabled: true }))
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Back' } })
    fireEvent.click(screen.getByRole('radio', { name: 'No music' }))
    fireEvent.click(screen.getByRole('radio', { name: 'My playlist' }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByText('Choose a playlist.')).toBeInTheDocument()
    expect(api.to('POST', '/api/jobs')).toHaveLength(0)
  })

  it('shows the form with No music even when MOWL lists no playlists', async () => {
    fakeApi(newClassRoutes({ soundless_enabled: true }, []))
    renderAt('/classes/new')
    expect(await screen.findByLabelText('Class name')).toBeInTheDocument()
    expect(screen.queryByText('No playlists found')).toBeNull()
    expect(screen.getByRole('radio', { name: 'No music' })).toBeInTheDocument()
  })

  it('keeps the No playlists found card while the flag is off', async () => {
    fakeApi(newClassRoutes({}, []))
    renderAt('/classes/new')
    expect(await screen.findByText('No playlists found')).toBeInTheDocument()
  })

  it('is hidden for Running, and a Running class never sends none', async () => {
    const api = fakeApi(newClassRoutes({ soundless_enabled: true, running_enabled: true }))
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Run' } })
    fireEvent.click(screen.getByRole('radio', { name: 'No music' }))
    fireEvent.click(screen.getByRole('radio', { name: 'Running' }))
    expect(screen.queryByRole('radio', { name: 'No music' })).toBeNull()
    // Back to Cycling: the choice is there again, and Running had put the music back to a playlist.
    fireEvent.click(screen.getByRole('radio', { name: 'Cycling' }))
    expect(screen.getByRole('radio', { name: 'No music' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('radio', { name: 'Running' }))
    // A playlist is needed.
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByText('Choose a playlist.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Tempo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/jobs')).toHaveLength(1)
    })
    const input = (api.to('POST', '/api/jobs')[0]?.body as { input: { music: unknown } }).input
    expect(input.music).toEqual({ source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_ID })
  })

  it('words the soundless refusals in plain language, never the code', async () => {
    for (const [why, text] of [
      ['feature_off', 'Classes with no music are not available right now. Choose a playlist, or try again later.'],
      ['soundless_not_evaluated', 'Classes with no music are not ready for your plan yet. Choose a playlist, or try again later.'],
    ] as const) {
      fakeApi({ ...newClassRoutes({ soundless_enabled: true }), 'POST /api/jobs': () => reason(409, why) })
      const view = renderAt('/classes/new')
      fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Silent' } })
      fireEvent.click(screen.getByRole('radio', { name: 'No music' }))
      fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
      expect(await screen.findByText(text)).toBeInTheDocument()
      expect(document.body).not.toHaveTextContent(why)
      view.unmount()
    }
  })

  it('keeps the Running sentence for feature_off when the form did not ask for No music', async () => {
    fakeApi({ ...newClassRoutes({ soundless_enabled: true }), 'POST /api/jobs': () => reason(409, 'feature_off') })
    renderAt('/classes/new')
    fireEvent.change(await screen.findByLabelText('Class name'), { target: { value: 'Mixed' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Tempo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Design my class' }))
    expect(await screen.findByText(/Running classes are not available right now/)).toBeInTheDocument()
  })
})

describe('the Designing page of a soundless job', () => {
  const INPUT = {
    title: 'Silent ride',
    brief: { length_min: 30, intensity: 'moderate', climbs: 0, sprints: 0, races: 0, ftp_test: null, style: '', text: '' },
    music: { source: 'none' },
    mowl_connection_id: MOWL_CONNECTION,
  }

  it('lists no music step', async () => {
    window.sessionStorage.setItem(`classbeat.design-input.${JOB_ID}`, JSON.stringify(INPUT))
    fakeApi({
      ...SESSION_ROUTES,
      [`GET /api/jobs/${JOB_ID}`]: jobBody('running'),
      [`GET /api/jobs/${JOB_ID}/events`]: openStream,
    })
    renderAt(`/jobs/${JOB_ID}`)
    const steps = await screen.findByRole('list', { name: 'Steps' })
    expect(within(steps).getAllByRole('listitem')).toHaveLength(1)
    expect(steps).toHaveTextContent('Building your class')
    expect(steps).not.toHaveTextContent(/playlist/i)
  })

  it('keeps the music step for a job on a playlist', async () => {
    window.sessionStorage.setItem(
      `classbeat.design-input.${JOB_ID}`,
      JSON.stringify({ ...INPUT, music: { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLIST_ID } }),
    )
    fakeApi({
      ...SESSION_ROUTES,
      [`GET /api/jobs/${JOB_ID}`]: jobBody('running'),
      [`GET /api/jobs/${JOB_ID}/events`]: openStream,
    })
    renderAt(`/jobs/${JOB_ID}`)
    const steps = await screen.findByRole('list', { name: 'Steps' })
    expect(within(steps).getAllByRole('listitem')).toHaveLength(2)
    expect(steps).toHaveTextContent('Reading your playlist')
  })

  it.each([
    ['feature_off', 'Classes with no music are switched off right now'],
    ['soundless_not_evaluated', 'Classes with no music are not ready for your plan yet'],
    ['invalid_input', 'The request could not be used'],
  ])('words an ended job with %s', async (category, title) => {
    window.sessionStorage.setItem(`classbeat.design-input.${JOB_ID}`, JSON.stringify(INPUT))
    fakeApi({
      ...SESSION_ROUTES,
      [`GET /api/jobs/${JOB_ID}`]: jobBody('failed', { error_category: category }),
    })
    renderAt(`/jobs/${JOB_ID}`)
    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(category)
    expect(document.body).not.toHaveTextContent(/swedebeat|running/i)
  })
})

describe('the job messages', () => {
  it('drops the music step only for a soundless design', () => {
    expect(designSteps('design_draft', false, true).map((s) => s.stage)).toEqual(['design'])
    expect(designSteps('design_draft', false).map((s) => s.stage)).toEqual(['music', 'design'])
    expect(designSteps('design_draft', false, false).map((s) => s.stage)).toEqual(['music', 'design'])
  })

  it('words feature_off per feature and invalid_input for a soundless request', () => {
    expect(failureText('feature_off', '', 'soundless').title).toMatch(/no music are switched off/)
    expect(failureText('feature_off', '', 'swedebeat').title).toMatch(/Swedebeat/)
    expect(failureText('invalid_input', '', 'soundless').detail).toMatch(/no music is for cycling/)
    expect(failureText('invalid_input').detail).toBe('Check the form and start again.')
    expect(failureText('soundless_not_evaluated').title).toMatch(/not ready for your plan/)
  })

  it('words a refused change of a soundless class only for that class', () => {
    expect(reviseFailureText('feature_off', true)).toMatch(/no music are switched off/)
    expect(reviseFailureText('feature_off', false)).not.toMatch(/music/)
    expect(reviseFailureText('soundless_not_evaluated', true)).toMatch(/not ready for your plan/)
  })
})

describe('the class page of a soundless class', () => {
  const PAGE = `/classes/${CLASS_ID}`

  function routes(soundless: boolean, messages: unknown[] = []) {
    return {
      ...SESSION_ROUTES,
      ...classRoutes({ cls: { soundless }, messages }),
      'GET /api/connections': { connections: [MOWL] },
    }
  }

  it('shows a No music chip, and none for a class with music', async () => {
    fakeApi(routes(true))
    const view = renderAt(PAGE)
    expect(await screen.findByTestId('soundless')).toHaveTextContent('No music')
    view.unmount()
    fakeApi(routes(false))
    renderAt(PAGE)
    await screen.findByTestId('tss')
    expect(screen.queryByTestId('soundless')).toBeNull()
  })

  it('never says the music changed, even when an answer carries the flag', async () => {
    const answers = [
      message(1, 'user', 'Make it harder'),
      message(2, 'assistant', '', { result_revision_id: REV1, change: { music_changed: true } }),
    ]
    fakeApi(routes(true, answers))
    const view = renderAt(PAGE)
    await screen.findByTestId('soundless')
    expect(screen.queryByTestId('music-changed')).toBeNull()
    expect(document.body).not.toHaveTextContent(/The music changed/)
    view.unmount()
    fakeApi(routes(false, answers))
    renderAt(PAGE)
    expect(await screen.findByTestId('music-changed')).toBeInTheDocument()
    expect(screen.getByText(/The music changed: the playlist in MOWL/)).toBeInTheDocument()
  })

  it('explains a songs-only request without Change songs or a playlist of your own', async () => {
    fakeApi({
      ...routes(true, [message(1, 'user', 'Different songs please')]),
      [`GET /api/jobs/${REVISE_JOB}`]: jobBody('failed', { id: REVISE_JOB, error_category: 'use_change_songs' }),
    })
    renderAt(PAGE)
    const outcome = await screen.findByTestId('use-change-songs')
    expect(outcome).toHaveTextContent('This class has no music; to add some, design a new class with a playlist.')
    expect(outcome).not.toHaveTextContent(/Use Change songs|playlist of your own/)
    expect(within(outcome).queryByRole('button')).toBeNull()
  })

  it('keeps the playlist-of-your-own sentence for a class with music and no song pick', async () => {
    fakeApi({
      ...routes(false, [message(1, 'user', 'Different songs please')]),
      [`GET /api/jobs/${REVISE_JOB}`]: jobBody('failed', { id: REVISE_JOB, error_category: 'use_change_songs' }),
    })
    renderAt(PAGE)
    expect(await screen.findByTestId('use-change-songs')).toHaveTextContent('This class uses a playlist of your own')
  })

  it('words the soundless refusal of a change in plain language', async () => {
    fakeApi({
      ...routes(true, [message(1, 'user', 'Harder')]),
      [`GET /api/jobs/${REVISE_JOB}`]: jobBody('failed', { id: REVISE_JOB, error_category: 'feature_off' }),
    })
    renderAt(PAGE)
    expect(await screen.findByRole('alert')).toHaveTextContent('Classes with no music are switched off right now. Nothing changed.')
  })
})
