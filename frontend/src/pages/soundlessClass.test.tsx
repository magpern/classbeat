import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { classRoutes } from '../test/classFixtures'
import { fakeApi } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'
import { HASH, LIST_ID, LIST_PATH, listBody } from '../test/songFixtures'

// ADR 0007 amendment of 2026-11-03, item 9 (#530): a soundless class has no songs and no mix, so
// the class page offers neither Change songs nor the Swedebeat section. The answers below carry an
// approved song list and an approved match on purpose: `soundless` alone must hide both.
const SONGS = { state: 'approved', job_id: null, revision_id: LIST_ID, content_hash: HASH, expires_at: null }
const MATCH = { revision_id: '77777777-7777-4777-8777-777777777777', approved: true }

function routes(soundless: boolean | undefined) {
  const cls: Record<string, unknown> = { songs: SONGS, swedebeat_match: MATCH }
  if (soundless !== undefined) cls.soundless = soundless
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls }),
    [`GET ${LIST_PATH}`]: listBody({ status: 'approved' }),
    'GET /api/connections': { connections: [] },
  } as Record<string, object>
}

describe('a soundless class page', () => {
  it('offers no Change songs and no Swedebeat section, and asks for neither', async () => {
    const api = fakeApi(routes(true))
    renderAt(`/classes/${CLASS_ID}`)
    await screen.findByText('Tuesday climb')
    expect(screen.queryByRole('button', { name: 'Change songs' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Change songs' })).toBeNull()
    expect(screen.queryByText(/Swedebeat/)).toBeNull()
    expect(api.calls.some((c) => c.path.includes('swedebeat'))).toBe(false)
  })

  it('still offers them for a class whose answer is not soundless (or has no such key)', async () => {
    for (const soundless of [false, undefined]) {
      fakeApi(routes(soundless))
      const view = renderAt(`/classes/${CLASS_ID}`)
      expect(await screen.findByRole('button', { name: 'Change songs' })).toBeInTheDocument()
      expect(await screen.findByRole('region', { name: 'Change songs' })).toBeInTheDocument()
      expect((await screen.findAllByText(/Swedebeat/)).length).toBeGreaterThan(0)
      view.unmount()
    }
  })
})
