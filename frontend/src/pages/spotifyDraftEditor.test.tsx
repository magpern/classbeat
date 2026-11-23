import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, REV2, classBody } from '../test/classFixtures'
import { EDITS_PATH, EDIT_PAGE, HASH2, editorRoutes, refusal } from '../test/editorApi'
import { fakeApi } from '../test/fakeApi'
import { renderAt } from '../test/render'

// A Spotify draft the server can edit (ADR 0042, #1424): the same editor as a studio draft's, without
// the studio parts (songs on the timeline, cut pins, anchors, rendered audio). Its music is heard in
// the Spotify audition panel. The backend and the player are fakes.

const A = `${CLASS_PATH}/preview/spotify`
const SHA = 'ab'.repeat(32)
const song = (piece: number) => ({
  piece,
  song: piece + 1,
  title: `Song ${String(piece + 1)}`,
  artist: 'Artist',
  length_ms: 200_000,
  analysis: { spotdown_job: 'job-1', sha256: SHA, timing_class: 'good' },
  alignment: { offset_ms: 0, source: 'spotdown' },
  correction: null,
})
const spotifyStatus = {
  status: 'connected',
  client_id: null,
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
  pool_consent: false,
  consent_version: null,
  consent_text: 'x',
  preview_granted: true,
}

function routes(extra: Record<string, unknown> = {}) {
  return editorRoutes({
    // Not a studio class: the class is not the studio's, the timeline answers 409 no_studio_draft.
    [`GET ${CLASS_PATH}`]: classBody(),
    [`GET ${CLASS_PATH}/timeline`]: () => refusal('no_studio_draft'),
    'GET /api/connections/spotify': spotifyStatus,
    [`GET ${A}/alignment`]: { revision_id: REV1, map_version: 3, crossfade_ms: 10_000, songs: [song(0), song(1), song(2)] },
    ...extra,
  })
}

async function boundary(n: number) {
  return screen.findByRole('slider', { name: new RegExp(`^End of block ${String(n)},`) })
}

describe('a Spotify draft in the editor', () => {
  it('opens the editor with its blocks and the audition panel, without the studio parts', async () => {
    fakeApi(routes())
    renderAt(EDIT_PAGE)
    await screen.findByTestId('editor-timeline')
    expect(within(screen.getByTestId('block-list')).getAllByRole('button')).toHaveLength(4)
    expect(await screen.findByTestId('spotify-audition')).toBeInTheDocument()
    expect(screen.getByTestId('editor-toolbar')).toBeInTheDocument()
    expect(screen.queryByTestId('cut-pins')).toBeNull()
    expect(screen.queryByTestId('readonly-edit_unavailable')).toBeNull()
    // The studio's rendered-audio Play is not there; the audition panel's Play is the only one.
    expect(screen.getAllByRole('button', { name: 'Play' })).toHaveLength(1)
    expect(screen.queryByText(/audio preview is not available/)).toBeNull()
  })

  it('offers no tie to the music in a block (an anchor would be refused)', async () => {
    fakeApi(routes())
    renderAt(EDIT_PAGE)
    await screen.findByTestId('editor-timeline')
    fireEvent.click(within(screen.getByTestId('block-list')).getAllByRole('button')[1] as HTMLElement)
    const inspector = await screen.findByTestId('block-inspector')
    expect(within(inspector).queryByTestId('anchor-start')).toBeNull()
    expect(within(inspector).queryByTestId('anchor-end')).toBeNull()
  })

  it('moves a boundary: one edit on the draft it was made on, then the class is read again', async () => {
    const api = fakeApi(routes({ [`POST ${EDITS_PATH}`]: { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] } }))
    renderAt(EDIT_PAGE)
    const handle = await boundary(1)
    handle.focus()
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    expect(api.to('POST', EDITS_PATH)[0]?.body).toEqual({ base_revision_id: REV1, edit: { op: 'drag', block: 0, delta_sec: 1 } })
    await waitFor(() => { expect(api.to('GET', `${CLASS_PATH}/overlay`).length).toBeGreaterThan(1) })
  })

  it('says in words that the music cannot be edited when the server refuses an audio edit', async () => {
    fakeApi(routes({ [`POST ${EDITS_PATH}`]: () => refusal('audio_edit_unavailable') }))
    renderAt(EDIT_PAGE)
    const handle = await boundary(1)
    handle.focus()
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
    expect(await screen.findByText(/music of a Spotify class cannot be edited here/)).toBeInTheDocument()
  })

  it('keeps an older Spotify draft (no stored map) read-only, with the audition panel', async () => {
    fakeApi(routes({ [`GET ${CLASS_PATH}/overlay`]: () => refusal('edit_unavailable') }))
    renderAt(EDIT_PAGE)
    expect(await screen.findByTestId('readonly-edit_unavailable')).toHaveTextContent(/made before Spotify classes could be edited here/)
    expect(await screen.findByTestId('spotify-audition')).toBeInTheDocument()
    expect(screen.queryByTestId('editor-toolbar')).toBeNull()
    expect(screen.queryByRole('slider')).toBeNull()
  })
})
