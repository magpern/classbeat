import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fakeApi, json, reason } from '../../test/fakeApi'
import { StudioSpotifySync } from './StudioSpotifySync'

const CLASS = '11111111-1111-4111-8111-111111111111'
const REV = '22222222-2222-4222-8222-222222222222'
const BASE = `/api/classes/${CLASS}/spotify-sync/${REV}`

function view(workingCopy: unknown, final: unknown = null) {
  return { working_copy: workingCopy, final }
}

describe('the Spotify sync state of a revision', () => {
  it('shows nothing before the revision has a state', async () => {
    const api = fakeApi({ [`GET ${BASE}`]: () => json(view(null)) })
    const { container } = render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="working_copy" />)
    await waitFor(() => { expect(api.to('GET', BASE)).toHaveLength(1) })
    expect(container).toBeEmptyDOMElement()
  })

  it('says a synced working playlist is up to date, with no action', async () => {
    fakeApi({ [`GET ${BASE}`]: () => json(view({ state: 'synced', detail: null })) })
    render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="working_copy" />)
    expect(await screen.findByText('up to date')).toBeInTheDocument()
    expect(screen.getByText('Spotify working playlist')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('shows a failure with its reason and Sync again, then follows the new sync', async () => {
    let state: unknown = { state: 'failed', detail: 'consent_missing' }
    const api = fakeApi({
      [`GET ${BASE}`]: () => json(view(null, state)),
      [`POST ${BASE}/final`]: () => {
        state = { state: 'synced', detail: null }
        return json({ job_id: '33333333-3333-4333-8333-333333333333', kind: 'spotify_sync', state: 'queued' }, 201)
      },
    })
    render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="final" />)
    expect(await screen.findByText('not updated')).toBeInTheDocument()
    expect(screen.getByText('Final Spotify playlist')).toBeInTheDocument()
    expect(screen.getByText(/Spotify did not allow the change/)).toBeInTheDocument()
    expect(screen.getByText('This never stops the SwedeBeat outputs.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Sync again' }))
    expect(await screen.findByText('up to date')).toBeInTheDocument()
    expect(api.to('POST', `${BASE}/final`)).toHaveLength(1)
  })

  it('words a list that a newer one took over, and an unknown failure', async () => {
    fakeApi({ [`GET ${BASE}`]: () => json(view({ state: 'failed', detail: 'superseded' })) })
    const { unmount } = render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="working_copy" />)
    expect(await screen.findByText(/follows a newer song list/)).toBeInTheDocument()
    unmount()
    fakeApi({ [`GET ${BASE}`]: () => json(view({ state: 'failed', detail: 'playlist_mismatch' })) })
    render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="working_copy" />)
    expect(await screen.findByText('Spotify did not take the change. Sync again.')).toBeInTheDocument()
  })

  it('words the final playlist on its own, and offers Sync again for an interrupted sync', async () => {
    fakeApi({ [`GET ${BASE}`]: () => json(view(null, { state: 'failed', detail: 'superseded' })) })
    const { unmount } = render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="final" />)
    expect(await screen.findByText(/follows a newer version of this class/)).toBeInTheDocument()
    expect(screen.queryByText(/working playlist|song list/)).toBeNull()
    unmount()
    fakeApi({ [`GET ${BASE}`]: () => json(view(null, { state: 'failed', detail: 'consent_missing' })) })
    const second = render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="final" />)
    expect(await screen.findByText(/Spotify did not allow the change/)).toBeInTheDocument()
    expect(screen.queryByText(/working playlist/)).toBeNull()
    second.unmount()
    fakeApi({ [`GET ${BASE}`]: () => json(view({ state: 'failed', detail: 'interrupted' })) })
    render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="working_copy" />)
    expect(await screen.findByText('The sync stopped before it finished. Sync again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sync again' })).toBeInTheDocument()
  })

  it('reads the state again when a press finds a sync already started', async () => {
    let reads = 0
    const api = fakeApi({
      [`GET ${BASE}`]: () => {
        reads += 1
        return json(view(reads === 1 ? { state: 'failed', detail: 'forbidden' } : { state: 'synced', detail: null }))
      },
      [`POST ${BASE}/working_copy`]: () => reason(409, 'not_failed'),
    })
    render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="working_copy" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Sync again' }))
    expect(await screen.findByText('up to date')).toBeInTheDocument()
    expect(api.to('POST', `${BASE}/working_copy`)).toHaveLength(1)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('says so when Sync again could not start', async () => {
    fakeApi({
      [`GET ${BASE}`]: () => json(view({ state: 'failed', detail: 'forbidden' })),
      [`POST ${BASE}/working_copy`]: () => reason(409, 'not_connected'),
    })
    render(<StudioSpotifySync classId={CLASS} revisionId={REV} target="working_copy" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Sync again' }))
    expect(await screen.findByText('The sync did not start. Try again.')).toBeInTheDocument()
  })
})
