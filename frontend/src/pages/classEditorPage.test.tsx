import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, REV2, classBody, revisionBody } from '../test/classFixtures'
import { CONTENT } from '../test/classFixtures'
import { CURRENT_PATH, EDITS_PATH, EDIT_PAGE, HASH1, HASH2, editorRoutes, overlayBody, refusal, staleAnswer, timelineBody } from '../test/editorApi'
import { fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'

const OVERLAY = `GET ${CLASS_PATH}/overlay`

async function boundary(n: number) {
  return screen.findByRole('slider', { name: new RegExp(`^End of block ${String(n)},`) })
}

async function ready() {
  await screen.findByTestId('editor-timeline')
}

describe('the editor page: editing by keyboard and mouse', () => {
  it('opens with the blocks, the songs and no way to play anything', async () => {
    fakeApi(editorRoutes())
    renderAt(EDIT_PAGE)
    await ready()
    expect(within(screen.getByTestId('block-list')).getAllByRole('button')).toHaveLength(4)
    expect(screen.getByRole('button', { name: /^Song 1: First song/ })).toBeInTheDocument()
    expect(screen.getByText(/Nothing plays here yet/)).toBeInTheDocument()
    expect(document.querySelector('audio, video')).toBeNull()
    expect(screen.queryByRole('button', { name: /play/i })).toBeNull()
    expect(screen.queryByTestId('cursor')).toBeNull()
  })

  it('moves a boundary with the keyboard: arrows choose, Enter sends one drag on the draft it was made on', async () => {
    const api = fakeApi(editorRoutes({ [`POST ${EDITS_PATH}`]: { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] } }))
    renderAt(EDIT_PAGE)
    const handle = await boundary(1)
    handle.focus()
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'ArrowRight', shiftKey: true })
    expect(api.to('POST', EDITS_PATH)).toHaveLength(0) // nothing is sent while choosing
    fireEvent.keyDown(handle, { key: 'Enter' })
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    expect(api.to('POST', EDITS_PATH)[0]?.body).toEqual({ base_revision_id: REV1, edit: { op: 'drag', block: 0, delta_sec: 7 } })
    // The answer carries no content: the page reads the class again.
    await waitFor(() => { expect(api.to('GET', `${CLASS_PATH}/overlay`).length).toBeGreaterThan(1) })
  })

  it('cancels a keyboard drag with Escape and sends nothing', async () => {
    const api = fakeApi(editorRoutes())
    renderAt(EDIT_PAGE)
    const handle = await boundary(1)
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    fireEvent.keyDown(handle, { key: 'Escape' })
    expect(api.to('POST', EDITS_PATH)).toHaveLength(0)
  })

  it('offers no handle at a timing-locked block and shows the lock', async () => {
    fakeApi(
      editorRoutes({
        [OVERLAY]: overlayBody(REV1, { records: [{ type: 'lock', segment_kind: 'main', span: [300_000, 900_000], scope: 'timing' }] }),
      }),
    )
    renderAt(EDIT_PAGE)
    await ready()
    expect(screen.queryByRole('slider', { name: /^End of block 2,/ })).toBeNull()
    expect(screen.queryByRole('slider', { name: /^End of block 1,/ })).toBeNull()
    expect(await screen.findByTestId('mark-lock')).toBeInTheDocument()
  })

  it('locks the selected block', async () => {
    const api = fakeApi(editorRoutes({ [`POST ${EDITS_PATH}`]: { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] } }))
    renderAt(EDIT_PAGE)
    await ready()
    fireEvent.click(within(screen.getByTestId('block-list')).getByRole('button', { name: /^2\. Climb/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Lock the block' }))
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    expect(api.to('POST', EDITS_PATH)[0]?.body).toEqual({ base_revision_id: REV1, edit: { op: 'lock', block: 1, scope: 'all' } })
  })

  it('changes an attribute, and a merge across zones asks first', async () => {
    const api = fakeApi(editorRoutes({ [`POST ${EDITS_PATH}`]: { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] } }))
    renderAt(EDIT_PAGE)
    await ready()
    fireEvent.click(within(screen.getByTestId('block-list')).getByRole('button', { name: /^2\. Climb/ }))
    fireEvent.click(await screen.findByLabelText('Standing'))
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    expect(api.to('POST', EDITS_PATH)[0]?.body).toEqual({ base_revision_id: REV1, edit: { op: 'set', block: 1, field: 'standing', value: false } })
    fireEvent.click(await screen.findByRole('button', { name: 'Merge with the next block' }))
    expect(screen.getByText(/different zones/)).toBeInTheDocument()
    expect(api.to('POST', EDITS_PATH)).toHaveLength(1) // nothing sent until confirmed
  })

  it('undoes with the step the overlay names, only while the page is on the current draft', async () => {
    const api = fakeApi(
      editorRoutes({
        [OVERLAY]: overlayBody(REV2, { undo: { revision_id: REV1, content_hash: HASH1 } }),
        [`GET ${CLASS_PATH}/timeline`]: timelineBody(REV2),
        [`POST ${CURRENT_PATH}`]: { revision_id: REV1 },
      }),
    )
    renderAt(EDIT_PAGE)
    await ready()
    expect(screen.getByRole('button', { name: 'Redo' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => { expect(api.to('POST', CURRENT_PATH)).toHaveLength(1) })
    expect(api.to('POST', CURRENT_PATH)[0]?.body).toEqual({ revision_id: REV1, content_hash: HASH1, expected_current_id: REV2 })
  })

  it('shows "Your edit kept (AI suggested ...)" and clears the override with its own action', async () => {
    const api = fakeApi(
      editorRoutes({
        [OVERLAY]: overlayBody(REV1, {
          records: [{ type: 'override', segment_kind: 'main', span: [300_000, 900_000], field: 'zone', value: 4, ai_value: 3 }],
        }),
        [`POST ${EDITS_PATH}`]: { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] },
      }),
    )
    renderAt(EDIT_PAGE)
    await ready()
    fireEvent.click(within(screen.getByTestId('block-list')).getByRole('button', { name: /^2\. Climb/ }))
    expect(await screen.findByText(/Your edit kept \(AI suggested/)).toBeInTheDocument()
    expect(screen.getByTestId('mark-override')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use the AI value for Zone' }))
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    expect(api.to('POST', EDITS_PATH)[0]?.body).toEqual({ base_revision_id: REV1, edit: { op: 'use_ai_value', block: 1, field: 'zone' } })
  })

  it('sets a cut pin by piece and edge, then confirms it', async () => {
    const api = fakeApi(
      editorRoutes({
        [OVERLAY]: overlayBody(REV1, { records: [{ type: 'pin', piece: 0, edge: 'end', value: 650, confirmed: false }] }),
        [`POST ${EDITS_PATH}`]: { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] },
      }),
    )
    renderAt(EDIT_PAGE)
    await ready()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Set the pin' }))[0] as HTMLElement)
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    expect(api.to('POST', EDITS_PATH)[0]?.body).toMatchObject({ base_revision_id: REV1, edit: { op: 'pin_set', piece: 0, mode: 'bar' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm cut' }))
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(2) })
    expect(api.to('POST', EDITS_PATH)[1]?.body).toEqual({ base_revision_id: REV1, edit: { op: 'pin_confirm', piece: 0, edge: 'end' } })
  })
})

describe('after an edit, until the page has read the draft it made', () => {
  it('offers no edit, so none can be sent on the draft before it', async () => {
    let release: () => void = () => undefined
    const late = new Promise<void>((resolve) => {
      release = resolve
    })
    let edited = false
    const api = fakeApi(
      editorRoutes({
        [OVERLAY]: () => (edited ? late.then(() => overlayBody(REV2)) : overlayBody(REV1)),
        [`GET ${CLASS_PATH}/timeline`]: () => timelineBody(edited ? REV2 : REV1),
        [`POST ${EDITS_PATH}`]: () => {
          edited = true
          return { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] }
        },
      }),
    )
    renderAt(EDIT_PAGE)
    fireEvent.click(within(await screen.findByTestId('block-list')).getByRole('button', { name: /^2\. Climb/ }))
    const lock = await screen.findByRole('button', { name: 'Lock the block' })
    const handle = await boundary(1)
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    await waitFor(() => { expect(lock).toBeDisabled() }) // the overlay read has not come back
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
    fireEvent.click(lock)
    expect(api.to('POST', EDITS_PATH)).toHaveLength(1)
    release()
    await waitFor(() => { expect(screen.getByRole('button', { name: 'Lock the block' })).toBeEnabled() })
    fireEvent.click(screen.getByRole('button', { name: 'Lock the block' }))
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(2) })
    expect(api.to('POST', EDITS_PATH)[1]?.body).toMatchObject({ base_revision_id: REV2 }) // the draft the first edit made
  })
})

describe('a stale draft (409 stale_revision)', () => {
  function staleServer(afterReload: unknown) {
    let moved = false
    const api = fakeApi(
      editorRoutes({
        [OVERLAY]: () => overlayBody(moved ? REV2 : REV1),
        [`GET ${CLASS_PATH}/timeline`]: () => timelineBody(moved ? REV2 : REV1),
        [`GET ${CLASS_PATH}/revisions/${REV2}`]: afterReload,
        [`GET ${CLASS_PATH}`]: () => classBody({ current_revision_id: moved ? REV2 : REV1 }),
        [`POST ${EDITS_PATH}`]: (body: unknown) => {
          const base = (body as { base_revision_id: string }).base_revision_id
          if (base === REV1) {
            moved = true
            return staleAnswer()
          }
          return json({ revision_id: 'a3333333-3333-4333-8333-333333333333', number: 3, content_hash: HASH2, hints: [] })
        },
      }),
    )
    return api
  }

  async function dragFirstBoundary() {
    const handle = await boundary(1)
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
  }

  it('reloads, keeps the unsent edit and applies it again on the new draft', async () => {
    const api = staleServer(revisionBody(REV2, 2))
    renderAt(EDIT_PAGE)
    await dragFirstBoundary()
    expect(await screen.findByText(/changed somewhere else/)).toBeInTheDocument()
    const again = await screen.findByRole('button', { name: 'Make my edit again' })
    expect(again).toBeEnabled()
    fireEvent.click(again)
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(2) })
    const [first, second] = api.to('POST', EDITS_PATH)
    expect(first?.body).toEqual({ base_revision_id: REV1, edit: { op: 'drag', block: 0, delta_sec: 1 } })
    expect(second?.body).toEqual({ base_revision_id: REV2, edit: { op: 'drag', block: 0, delta_sec: 1 } })
  })

  it('does not offer the edit again when the block it was for has changed', async () => {
    const changed = {
      ...CONTENT,
      segments: CONTENT.segments.map((s) => (s.kind === 'warmup' ? { ...s, intervals: [{ sec: 240, zone: 3, rpm: [80, 90], standing: false, cue: '' }] } : s)),
    }
    staleServer(revisionBody(REV2, 2, changed))
    renderAt(EDIT_PAGE)
    await dragFirstBoundary()
    expect(await screen.findByText(/the block it was for has changed/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Make my edit again' })).toBeNull()
  })

  it('drops the kept edit on request', async () => {
    staleServer(revisionBody(REV2, 2))
    renderAt(EDIT_PAGE)
    await dragFirstBoundary()
    fireEvent.click(await screen.findByRole('button', { name: 'Drop it' }))
    expect(screen.queryByTestId('stale-notice')).toBeNull()
  })
})

describe('a refused edit', () => {
  it('says why in words, changes nothing and does not reload as if stale', async () => {
    const api = fakeApi(editorRoutes({ [`POST ${EDITS_PATH}`]: () => refusal('block_locked') }))
    renderAt(EDIT_PAGE)
    const handle = await boundary(1)
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
    expect(await screen.findByText('That block is locked. Unlock it first.')).toBeInTheDocument()
    expect(screen.queryByTestId('stale-notice')).toBeNull()
    expect(api.to('GET', `${CLASS_PATH}/overlay`)).toHaveLength(1)
  })

  it('names an unknown reason without its code', async () => {
    fakeApi(editorRoutes({ [`POST ${EDITS_PATH}`]: () => refusal('some_new_code') }))
    renderAt(EDIT_PAGE)
    const handle = await boundary(1)
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
    expect(await screen.findByText('The edit was refused. Nothing changed.')).toBeInTheDocument()
    expect(screen.queryByText(/some_new_code/)).toBeNull()
  })

  it('explains mix_not_applied and links back to the class', async () => {
    fakeApi(editorRoutes({ [`POST ${EDITS_PATH}`]: () => refusal('mix_not_applied') }))
    renderAt(EDIT_PAGE)
    const handle = await boundary(1)
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Enter' })
    expect(await screen.findByText(/came from a chat change/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Open the class' }).length).toBeGreaterThan(0)
  })
})

describe('a draft that cannot be edited opens read-only, never as a broken editor', () => {
  const cases: [string, Record<string, unknown>, RegExp][] = [
    ['edit_unavailable', { [OVERLAY]: () => refusal('edit_unavailable') }, /Classes with music from Spotify are shown as they are/],
    ['mix_not_applied', { [OVERLAY]: () => refusal('mix_not_applied') }, /Apply these choices/],
    ['music_changed', { [OVERLAY]: () => refusal('music_changed') }, /analysis of this class/],
    ['no_proposal', { [OVERLAY]: () => refusal('no_proposal') }, /no music design to edit/],
    ['an unknown reason', { [OVERLAY]: () => refusal('something_new') }, /cannot be edited here yet/],
    ['a draft whose analysis expired', { [`GET ${CLASS_PATH}/timeline`]: () => refusal('music_changed') }, /analysis of this class/],
    ['an open job', { [`GET ${CLASS_PATH}`]: classBody({ open_job_id: 'b1111111-1111-4111-8111-111111111111', open_job: { id: 'b1111111-1111-4111-8111-111111111111', kind: 'revise_draft', state: 'running' } }) }, /A change to this class is being made/],
    ['no draft yet', { [`GET ${CLASS_PATH}`]: classBody({ current_revision_id: null, current_revision_number: null, revision_count: 0 }) }, /no version to edit yet/],
  ]
  for (const [name, routes, message] of cases) {
    it(`${name}: a sentence, a way back, no handles and no buttons that would fail`, async () => {
      const api = fakeApi(editorRoutes(routes))
      renderAt(EDIT_PAGE)
      expect(await screen.findByText(message)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Open the class' })).toBeInTheDocument()
      expect(screen.queryByRole('slider')).toBeNull()
      expect(screen.queryByTestId('editor-toolbar')).toBeNull()
      expect(api.to('POST', EDITS_PATH)).toHaveLength(0)
    })
  }

  it('offers the way forward that exists for an expired analysis: change songs', async () => {
    fakeApi(editorRoutes({ [OVERLAY]: () => refusal('music_changed') }))
    renderAt(EDIT_PAGE)
    expect(await screen.findByRole('link', { name: 'Change songs' })).toBeInTheDocument()
  })

  it('says so when the class is not found', async () => {
    fakeApi(editorRoutes({ [`GET ${CLASS_PATH}`]: () => json({ detail: { reason: 'not_found' } }, 404) }))
    renderAt(EDIT_PAGE)
    expect(await screen.findByText('This class was not found.')).toBeInTheDocument()
  })
})

describe('the cursor', () => {
  it('appears only after a song is chosen and is the transport state, with no audio', async () => {
    fakeApi(editorRoutes())
    renderAt(EDIT_PAGE)
    await ready()
    expect(screen.getByText('No cursor.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Song 2: Second song/ }))
    const cursor = await screen.findByTestId('cursor')
    expect(Number(cursor.getAttribute('data-cursor-ms'))).toBeGreaterThanOrEqual(674_000)
    expect(Number(cursor.getAttribute('data-cursor-ms'))).toBeLessThan(680_000)
    expect(screen.getByText(/^Cursor at /)).toBeInTheDocument()
    expect(document.querySelector('audio, video')).toBeNull()
  })

  it('shows what a song comes from, and "not recorded" for what the API does not give', async () => {
    fakeApi(editorRoutes())
    renderAt(EDIT_PAGE)
    await ready()
    fireEvent.focus(screen.getByRole('button', { name: /^Song 1: First song/ }))
    expect(await screen.findByText('Position in the song')).toBeInTheDocument()
    expect(screen.getAllByText('not recorded').length).toBeGreaterThan(0)
  })
})
