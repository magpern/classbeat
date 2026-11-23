import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { classRoutes } from '../test/classFixtures'
import { fakeApi } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// #1453: a Spotify class's page links to the editor, and no other class gets a link the editor would refuse.

const PAGE = `/classes/${CLASS_ID}`
const JOB = { id: 'b1111111-1111-4111-8111-111111111111', kind: 'revise_draft', state: 'running' }

async function open(cls: Record<string, unknown>) {
  fakeApi({ ...SESSION_ROUTES, ...classRoutes({ cls }) })
  renderAt(PAGE)
  await screen.findByRole('heading', { name: 'Tuesday climb' })
}

describe('the editor link on a Spotify class page', () => {
  it('is shown for a Spotify class with a version and no open job, pointing at the editor', async () => {
    await open({ spotify: true })
    expect(await screen.findByRole('link', { name: 'Open the editor' })).toHaveAttribute('href', `/en${PAGE}/edit`)
  })

  it.each([
    ['a SwedeBeat-mix class', {}],
    ['a soundless class', { soundless: true }],
    ['a Spotify class with an open job', { spotify: true, open_job_id: JOB.id, open_job: JOB }],
    ['a Spotify class with no version yet', { spotify: true, current_revision_id: null, current_revision_number: null, revision_count: 0 }],
  ])('is absent for %s', async (_name, cls) => {
    await open(cls)
    expect(screen.queryByRole('link', { name: 'Open the editor' })).toBeNull()
  })
})
