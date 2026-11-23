import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, REV2 } from '../test/classFixtures'
import { CURRENT_PATH, EDITS_PATH, EDIT_PAGE, HASH1, HASH2, PREVIEW_PATH, editorRoutes, overlayBody, previewRoutes, refusal, timelineBody } from '../test/editorApi'
import { fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'

// Hardening of the editor page (E3 review findings): a refused undo reloads, focus survives an
// edit, the reads must agree on one draft, no audition where nothing is audible, and a song says
// where the cursor is in its own label.

const OVERLAY = `GET ${CLASS_PATH}/overlay`
const TIMELINE = `GET ${CLASS_PATH}/timeline`
const handle = (n: number) => screen.findByRole('slider', { name: new RegExp(`^End of block ${String(n)},`) })

describe('a refused undo (409 hash_mismatch)', () => {
  it('reloads the page, so "It has been reloaded" is true', async () => {
    const api = fakeApi(
      editorRoutes({
        [OVERLAY]: overlayBody(REV2, { undo: { revision_id: REV1, content_hash: HASH1 } }),
        [TIMELINE]: timelineBody(REV2),
        [`POST ${CURRENT_PATH}`]: () => refusal('hash_mismatch'),
      }),
    )
    renderAt(EDIT_PAGE)
    await screen.findByTestId('editor-timeline')
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(await screen.findByText(/It has been reloaded/)).toBeInTheDocument()
    await waitFor(() => { expect(api.to('GET', `${CLASS_PATH}/overlay`)).toHaveLength(2) })
  })
})

describe('keyboard focus after an edit', () => {
  it('is back on the boundary handle that was moved, once the edit has been read back', async () => {
    const api = fakeApi(editorRoutes({ [`POST ${EDITS_PATH}`]: { revision_id: REV2, number: 2, content_hash: HASH2, hints: [] } }))
    renderAt(EDIT_PAGE)
    const before = await handle(1)
    before.focus()
    expect(document.activeElement).toBe(before)
    fireEvent.keyDown(before, { key: 'ArrowRight' })
    fireEvent.keyDown(before, { key: 'Enter' })
    await waitFor(() => { expect(api.to('POST', EDITS_PATH)).toHaveLength(1) })
    await waitFor(() => { expect(api.to('GET', `${CLASS_PATH}/overlay`)).toHaveLength(2) })
    await waitFor(() => {
      expect(document.activeElement).toBe(document.querySelector('[data-handle="0"]'))
    })
    expect(document.activeElement?.getAttribute('data-handle')).toBe('0')
  })
})

describe('reads of two drafts', () => {
  it('reads again while the timeline is of another draft than the overlay', async () => {
    let reads = 0
    const api = fakeApi(
      editorRoutes({
        [OVERLAY]: () => overlayBody(REV1),
        [TIMELINE]: () => {
          reads += 1
          return json(timelineBody(reads === 1 ? REV2 : REV1)) // the first read caught the class between two drafts
        },
      }),
    )
    renderAt(EDIT_PAGE)
    await screen.findByTestId('editor-timeline')
    expect(api.to('GET', `${CLASS_PATH}/timeline`)).toHaveLength(2)
  })

  it('fails with a retry when the reads never agree', async () => {
    fakeApi(editorRoutes({ [OVERLAY]: () => overlayBody(REV1), [TIMELINE]: () => json(timelineBody(REV2)) }))
    renderAt(EDIT_PAGE)
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument()
    expect(screen.queryByTestId('editor-timeline')).toBeNull()
  })
})

describe('Play where no song is audible', () => {
  it('starts no audition, never piece 0 at source 0', async () => {
    const only = timelineBody(REV1)
    only.tracks = only.tracks.slice(1) // the first song is gone: nothing is audible at 0:00
    const api = fakeApi(editorRoutes(previewRoutes({ [TIMELINE]: only })))
    renderAt(EDIT_PAGE)
    await screen.findByTestId('editor-timeline')
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(api.to('POST', `${PREVIEW_PATH}/sessions`)).toHaveLength(0)
    expect(screen.getByText('No cursor.')).toBeInTheDocument()
  })
})

describe('a song in the lane', () => {
  it('says in its own label whether the cursor is in it', async () => {
    fakeApi(editorRoutes(previewRoutes()))
    renderAt(EDIT_PAGE)
    await screen.findByTestId('editor-timeline')
    const first = screen.getByRole('button', { name: /^Song 1: First song/ })
    expect(first.getAttribute('aria-label')).not.toContain('The cursor is in this song')
    fireEvent.click(first)
    await waitFor(() => { expect(document.querySelector('audio[src]')).not.toBeNull() })
    document.querySelector('audio[src]')?.dispatchEvent(new Event('playing'))
    await screen.findByTestId('cursor')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Song 1: First song.*The cursor is in this song\.$/ })).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /^Song 2: Second song/ }).getAttribute('aria-label')).not.toContain('The cursor is in this song')
  })
})
