import { screen, waitFor, within } from '@testing-library/react'
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
    expect(screen.getByRole('link', { name: 'Design a class' })).toHaveAttribute('href', '/en/classes/new')
    expect(screen.getByRole('region', { name: 'No classes yet' })).toHaveTextContent(
      'Describe a class and we design it from your playlist.',
    )
    const img = document.querySelector('[data-slot="emptyClasses"] img')
    expect(img).toHaveAttribute('alt', '')
    expect(img).toHaveAttribute('aria-hidden', 'true')
    expect(img).toHaveAttribute('loading', 'lazy')
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
    expect(first).toHaveAttribute('href', `/en/classes/${CLASS_ID}`)
    expect(document.querySelector('main img')).toBeNull()
    expect(screen.getByText('Version 1, not saved')).toBeInTheDocument()
    expect(screen.getByText('Being designed')).toBeInTheDocument()
  })

  it('offers Duplicate on a card with a version and not on one still being designed', async () => {
    renderAtWith([
      classBody({ title: 'Hills' }),
      classBody({ id: SECOND, title: 'Second', current_revision_id: null, current_revision_number: null, profile: [] }),
    ])
    const button = await screen.findByRole('button', { name: 'Duplicate Hills' })
    expect(button.textContent).toBe('Duplicate')
    expect(screen.queryByRole('button', { name: 'Duplicate Second' })).toBeNull()
  })

  it('offers Delete on every card, a failed one with no version included, and removes it', async () => {
    const failed = classBody({ id: SECOND, title: 'Failed', current_revision_id: null, current_revision_number: null, profile: [] })
    let remaining = [classBody({ title: 'Hills' }), failed]
    const api = fakeApi({
      ...SESSION_ROUTES,
      'GET /api/classes': () => ({ classes: remaining }),
      [`DELETE /api/classes/${SECOND}`]: () => {
        remaining = remaining.filter((c) => c.id !== SECOND)
        return { deleted: true, mowl_workouts_kept: 0, note: 'x' }
      },
    })
    renderAt('/classes')
    expect(await screen.findByRole('button', { name: 'Delete class Hills' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete class Failed' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, delete' }))
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Failed' })).toBeNull()
    })
    expect(api.calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Hills' })).toBeInTheDocument()
  })

  it('names the delete dialog by the confirmation heading, the only title in it', async () => {
    renderAtWith([classBody({ title: 'Hills' })])
    await userEvent.click(await screen.findByRole('button', { name: 'Delete class Hills' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete class' })
    expect(dialog).toHaveAttribute('aria-labelledby', 'delete-title')
    expect(within(dialog).getAllByRole('heading')).toHaveLength(1)
    expect(document.querySelectorAll('#delete-title')).toHaveLength(1)
  })

  it('shows a mini profile, the length and the TSS on a card', async () => {
    renderAtWith([classBody()])
    expect(await screen.findByRole('img', { name: /Class profile, 17 min, 3 intervals/ })).toBeInTheDocument()
    expect(screen.getByText('17 min')).toBeInTheDocument()
    // 300 s z2, 90 s z5, 600 s z3: (126 + 107.73 + 450) / 36 = 18.99 -> 19
    expect(screen.getByText('19')).toBeInTheDocument()
    expect(screen.queryByText(/≈/)).toBeNull()
  })

  it('marks a TSS with a zone 7 interval as approximate, with the note as screen-reader text', async () => {
    renderAtWith([classBody({ profile: [[3, 600], [7, 30]], tss_approximate: true })])
    const note = await screen.findByText(/^\d+(\.\d)?, approximate: zone 7 has no exact TSS weight$/)
    expect(note).toHaveClass('sr-only')
    // The visible mark is hidden from screen readers; the note is the text they get.
    const mark = screen.getByText(/^≈ \d/)
    expect(mark).toHaveAttribute('aria-hidden', 'true')
    expect(note.parentElement).toContainElement(mark)
    expect(note.parentElement).not.toHaveAttribute('aria-label')
  })

  it('skips a malformed interval and keeps the rest of the profile and the list', async () => {
    renderAtWith([
      classBody({ profile: [[2.5, 300], [3, 600], [4, 90.5], 'junk', [5], [3, -1], [4, 300]] }),
      classBody({ id: SECOND, title: 'Other' }),
    ])
    expect(await screen.findByRole('img', { name: /Class profile, 15 min, 2 intervals/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Other' })).toBeInTheDocument()
  })

  it('shows saved state and "unsaved changes"', async () => {
    renderAtWith([
      classBody({ saved: SAVED }),
      classBody({ id: SECOND, title: 'Other', saved: { ...SAVED, latest_up_to_date: false } }),
    ])
    expect(await screen.findByText('Version 1 in MOWL')).toBeInTheDocument()
    expect(screen.getByText('Version 1, unsaved changes')).toBeInTheDocument()
  })

  it('filters with All, Saved to MOWL and Drafts, and searches by title on the client', async () => {
    const api = renderAtWith([
      classBody({ title: 'Hills', saved: SAVED }),
      classBody({ id: SECOND, title: 'Intervals' }),
      classBody({ id: THIRD, title: 'Endurance ride' }),
    ])
    const titles = () =>
      screen.getAllByRole('link').map((link) => link.textContent).filter((text) => !text.startsWith('Open class') && /^(Hills|Intervals|Endurance ride)$/.test(text))
    const tab = (name: string) => screen.getByRole('radio', { name: new RegExp(`^${name}`) })
    await screen.findByRole('link', { name: 'Hills' })
    expect(titles()).toEqual(['Hills', 'Intervals', 'Endurance ride'])
    expect(tab('All')).toBeChecked()
    expect(tab('All')).toHaveTextContent('3')
    expect(tab('Saved to MOWL')).toHaveTextContent('1')

    await userEvent.click(tab('Saved to MOWL'))
    expect(titles()).toEqual(['Hills'])
    await userEvent.click(tab('Drafts'))
    expect(titles()).toEqual(['Intervals', 'Endurance ride'])
    await userEvent.click(tab('All'))

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search your classes' }), 'ride')
    expect(titles()).toEqual(['Endurance ride'])
    await userEvent.clear(screen.getByRole('searchbox', { name: 'Search your classes' }))
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search your classes' }), 'zzz')
    expect(screen.getByText(/No class matches/)).toBeInTheDocument()
    // Searching and filtering never ask the server again.
    expect(api.to('GET', '/api/classes')).toHaveLength(1)
  })

  it('shows the newest class with a version large and the rest as cards, drafts with a ghost and Continue', async () => {
    renderAtWith([
      classBody({ id: SECOND, title: 'Draft one', current_revision_id: null, current_revision_number: null, profile: [], updated_at: '2026-11-09T10:00:00Z' }),
      classBody({ title: 'Older', updated_at: '2026-11-01T10:00:00Z' }),
      classBody({ id: THIRD, title: 'Newest', updated_at: '2026-11-05T10:00:00Z' }),
    ])
    const feature = await screen.findByRole('article')
    expect(within(feature).getByRole('heading', { name: 'Newest' })).toBeInTheDocument()
    expect(within(feature).getByRole('link', { name: 'Open class' })).toHaveAttribute('href', `/en/classes/${THIRD}`)
    expect(within(feature).getByText('Latest design')).toBeInTheDocument()
    expect(within(feature).getByText('Time')).toBeInTheDocument()
    expect(within(feature).queryByText(/Make a/)).toBeNull()
    const cards = screen.getAllByRole('listitem')
    // The ghost cards are the ones without a version; the feature is not repeated in the grid.
    const draft = cards.find((li) => within(li).queryByRole('link', { name: 'Draft one' }))!
    expect(draft).toHaveTextContent('Continue')
    expect(draft.querySelector('.ghost-shimmer')).not.toBeNull()
    expect(cards.some((li) => within(li).queryByRole('link', { name: 'Newest' }))).toBe(false)
    expect(within(cards.find((li) => within(li).queryByRole('link', { name: 'Older' }))!).getByRole('img')).toBeInTheDocument()
  })

  it('quotes the request and shows the song count on the feature card only', async () => {
    renderAtWith([
      classBody({ id: THIRD, title: 'Newest', updated_at: '2026-11-05T10:00:00Z', request: 'A hilly 45 minutes <b>no</b>', song_count: 12 }),
      classBody({ title: 'Older', updated_at: '2026-11-01T10:00:00Z', request: 'Older ask', song_count: 9 }),
    ])
    const feature = await screen.findByRole('article')
    const quote = within(feature).getByText('A hilly 45 minutes <b>no</b>')
    expect(quote.tagName).toBe('BLOCKQUOTE')
    expect(quote).toHaveClass('line-clamp-2')
    expect(within(feature).getByText('Songs').nextElementSibling).toHaveTextContent('12')
    // The grid cards stay as they were: no quote, no Songs.
    const older = screen.getAllByRole('listitem').find((li) => within(li).queryByRole('link', { name: 'Older' }))!
    expect(within(older).queryByText('Older ask')).toBeNull()
    expect(within(older).queryByText('Songs')).toBeNull()
  })

  it('shows no quote and no Songs on the feature card without a request or a song count', async () => {
    renderAtWith([classBody({ title: 'Plain', request: null, song_count: null })])
    const feature = await screen.findByRole('article')
    expect(within(feature).getByText('Time')).toBeInTheDocument()
    expect(feature.querySelector('blockquote')).toBeNull()
    expect(within(feature).queryByText('Songs')).toBeNull()
  })

  it('shows a stats strip: classes, saved to MOWL and classes left this month', async () => {
    renderAtWith([classBody({ title: 'Hills', saved: SAVED }), classBody({ id: SECOND, title: 'Other' })])
    await screen.findByRole('link', { name: 'Hills' })
    const dl = document.querySelector('dl[aria-label="Your classes in numbers"]')!
    expect(dl).toHaveTextContent(/Classes\s*2/)
    expect(dl).toHaveTextContent(/Saved to MOWL\s*1/)
    await waitFor(() => {
      expect(dl).toHaveTextContent(/Left this month\s*2/)
    })
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
