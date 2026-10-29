import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { classBody } from '../test/classFixtures'
import { fakeApi, json } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

describe('My classes', () => {
  it('says so when there are none and links to the form', async () => {
    fakeApi({ ...SESSION_ROUTES, 'GET /api/classes': { classes: [] } })
    renderAt('/classes')
    expect(await screen.findByRole('heading', { name: 'No classes yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Design a class' })).toHaveAttribute('href', '/classes/new')
  })

  it('lists the classes as links with their state, titles as text', async () => {
    const { container } = renderAtWith([
      classBody({ title: 'Hills <img src=x onerror=alert(1)>' }),
      classBody({
        id: '99999999-9999-4999-8999-999999999999',
        title: 'Second',
        current_revision_id: null,
        current_revision_number: null,
        open_job: { id: 'b1111111-1111-4111-8111-111111111111', kind: 'design_draft', state: 'running' },
      }),
    ])
    const first = await screen.findByRole('link', { name: 'Hills <img src=x onerror=alert(1)>' })
    expect(first).toHaveAttribute('href', `/classes/${CLASS_ID}`)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/Version 1 of 1/)).toBeInTheDocument()
    expect(screen.getByText(/Being designed/)).toBeInTheDocument()
  })

  it('shows saved state', async () => {
    renderAtWith([
      classBody({
        saved: { count: 1, latest_revision_number: 1, latest_saved_at: '2026-10-29T11:00:00Z', latest_up_to_date: false },
      }),
    ])
    expect(await screen.findByText(/saved, with newer changes/)).toBeInTheDocument()
  })

  it('offers to try again when the list cannot be loaded', async () => {
    fakeApi({ ...SESSION_ROUTES, 'GET /api/classes': json({}, 500) })
    renderAt('/classes')
    expect(await screen.findByText('Your classes could not be loaded.')).toBeInTheDocument()
  })
})

function renderAtWith(classes: unknown[]) {
  fakeApi({ ...SESSION_ROUTES, 'GET /api/classes': { classes } })
  return renderAt('/classes')
}
