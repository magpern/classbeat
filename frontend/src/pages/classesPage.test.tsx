import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { classBody } from '../test/classFixtures'
import { fakeApi, json } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

const SECOND = '99999999-9999-4999-8999-999999999999'
const THIRD = '88888888-8888-4888-8888-888888888888'

const SAVED = { count: 1, latest_revision_number: 1, latest_saved_at: '2026-10-29T11:00:00Z', latest_up_to_date: true }

describe('My classes', () => {
  it('says so when there are none and offers the form from the page header', async () => {
    fakeApi({ ...SESSION_ROUTES, 'GET /api/classes': { classes: [] } })
    renderAt('/classes')
    expect(await screen.findByRole('heading', { name: 'No classes yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Design a class' })).toHaveAttribute('href', '/classes/new')
  })

  it('lists the classes as cards with their state, titles as text', async () => {
    renderAtWith([
      classBody({ title: 'Hills <img src=x onerror=alert(1)>' }),
      classBody({
        id: SECOND,
        title: 'Second',
        current_revision_id: null,
        current_revision_number: null,
        profile: [],
        open_job: { id: 'b1111111-1111-4111-8111-111111111111', kind: 'design_draft', state: 'running' },
      }),
    ])
    const first = await screen.findByRole('link', { name: 'Hills <img src=x onerror=alert(1)>' })
    expect(first).toHaveAttribute('href', `/classes/${CLASS_ID}`)
    expect(document.querySelector('main img')).toBeNull()
    expect(screen.getByText('Version 1, not saved to MOWL')).toBeInTheDocument()
    expect(screen.getByText('Being designed')).toBeInTheDocument()
  })

  it('shows a mini profile, the length and the TSS on a card', async () => {
    renderAtWith([classBody()])
    expect(await screen.findByRole('img', { name: /Class profile, 17 min, 3 intervals/ })).toBeInTheDocument()
    expect(screen.getByText('17 min')).toBeInTheDocument()
    // 300 s z2, 90 s z5, 600 s z3: (126 + 107.73 + 450) / 36 = 18.99 -> 19
    expect(screen.getByText('19')).toBeInTheDocument()
    expect(screen.queryByText(/≈/)).toBeNull()
  })

  it('marks a TSS with a zone 7 interval as approximate, with the note as visually hidden text', async () => {
    renderAtWith([classBody({ profile: [[3, 600], [7, 30]] })])
    const note = await screen.findByText(/^\d+(\.\d)?, approximate: zone 7 has no exact TSS weight$/)
    expect(note).toHaveClass('sr-only')
    expect(note.parentElement).toHaveTextContent(/^≈ \d/)
    expect(note.previousElementSibling).toHaveAttribute('aria-hidden', 'true')
  })

  it('shows saved state and "changes not in MOWL yet"', async () => {
    renderAtWith([
      classBody({ saved: SAVED }),
      classBody({ id: SECOND, title: 'Other', saved: { ...SAVED, latest_up_to_date: false } }),
    ])
    expect(await screen.findByText('Version 1 saved to MOWL')).toBeInTheDocument()
    expect(screen.getByText('Version 1 has changes not in MOWL yet')).toBeInTheDocument()
  })

  it('filters with All, Saved to MOWL and Drafts, and searches by title on the client', async () => {
    const api = renderAtWith([
      classBody({ title: 'Hills', saved: SAVED }),
      classBody({ id: SECOND, title: 'Intervals' }),
      classBody({ id: THIRD, title: 'Endurance ride' }),
    ])
    const titles = () => screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    await screen.findByRole('link', { name: 'Hills' })
    expect(titles()).toEqual(['Hills', 'Intervals', 'Endurance ride'])
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(screen.getByRole('button', { name: 'Saved to MOWL' }))
    expect(titles()).toEqual(['Hills'])
    await userEvent.click(screen.getByRole('button', { name: 'Drafts' }))
    expect(titles()).toEqual(['Intervals', 'Endurance ride'])
    await userEvent.click(screen.getByRole('button', { name: 'All' }))

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search your classes' }), 'ride')
    expect(titles()).toEqual(['Endurance ride'])
    await userEvent.clear(screen.getByRole('searchbox', { name: 'Search your classes' }))
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search your classes' }), 'zzz')
    expect(screen.getByText(/No class matches/)).toBeInTheDocument()
    // Searching and filtering never ask the server again.
    expect(api.to('GET', '/api/classes')).toHaveLength(1)
  })

  it('works when the server sends no profile (an older backend)', async () => {
    const without: Record<string, unknown> = classBody()
    delete without.profile
    renderAtWith([without])
    expect(await screen.findByRole('link', { name: 'Tuesday climb' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'No class profile yet' })).toBeInTheDocument()
  })

  it('offers to try again when the list cannot be loaded', async () => {
    fakeApi({ ...SESSION_ROUTES, 'GET /api/classes': json({}, 500) })
    renderAt('/classes')
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('Your classes could not be loaded.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

function renderAtWith(classes: unknown[]) {
  const api = fakeApi({ ...SESSION_ROUTES, 'GET /api/classes': { classes } })
  renderAt('/classes')
  return api
}
