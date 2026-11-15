import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, classRoutes } from '../test/classFixtures'
import { fakeApi } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody, sse } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// What the player holds of a class (ADR 0034 section 6, #1068): the class page's line after a
// reload, the delete card's warning, and the removal the deletion started.

const PAGE = `/classes/${CLASS_ID}`
const PLAYER = '/api/connections/player'
const REMOVE_JOB = 'a3333333-3333-4333-8333-333333333333'

const status = (value: string) => ({
  status: value,
  last_error: null,
  connected_at: value === 'not_connected' ? null : '2026-11-10T10:00:00Z',
  link_enabled: true,
})
const SENT = { sent_revision_id: REV1, sent_revision_number: 1 }

function start(player: unknown, cls: Record<string, unknown> = {}, more: Record<string, object | (() => unknown)> = {}) {
  return fakeApi({
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { player, ...cls } }),
    [`GET ${PLAYER}`]: status('connected'),
    ...more,
  })
}

describe('the class page line for the player', () => {
  it('says not sent when the class never went to a connected player', async () => {
    start(null)
    renderAt(PAGE)
    expect(await screen.findByTestId('player-state')).toHaveTextContent('Not sent to the player.')
  })

  it('says which version was sent, from the answer alone', async () => {
    start(SENT)
    renderAt(PAGE)
    expect(await screen.findByTestId('player-state')).toHaveTextContent('On the player: version 1.')
  })

  it('says when the current version is newer than the one sent', async () => {
    start(SENT, { current_revision_number: 3 })
    renderAt(PAGE)
    expect(await screen.findByTestId('player-state')).toHaveTextContent(
      'On the player: version 1. Version 3 is newer.',
    )
  })

  it('shows nothing for a class that is not sent while the player is not connected', async () => {
    start(null, {}, { [`GET ${PLAYER}`]: status('not_connected') })
    renderAt(PAGE)
    await screen.findByTestId('class-header')
    await screen.findByRole('link', { name: 'Connect classbeat-player to send this class' })
    expect(screen.queryByTestId('player-state')).toBeNull()
  })
})

async function openDelete() {
  await userEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  await userEvent.click(await screen.findByRole('menuitem', { name: 'Delete class' }))
  return screen.findByRole('region', { name: 'Delete class' })
}

const removalJob = (state: string, extra: Record<string, unknown> = {}) =>
  jobBody(state, { id: REMOVE_JOB, kind: 'player_remove', class_id: null, ...extra })

describe('deleting a class that is on the player', () => {
  it('does not mention a removal when the class was never sent', async () => {
    start(null)
    renderAt(PAGE)
    const card = await openDelete()
    expect(within(card).queryByText(/removes it there/)).toBeNull()
  })

  it('says a removal will be sent and follows its job to the end', async () => {
    const path = `/api/jobs/${REMOVE_JOB}`
    start(SENT, {}, {
      [`DELETE ${CLASS_PATH}`]: {
        deleted: true,
        mowl_workouts_kept: 0,
        note: 'x',
        player_removal_started: true,
        player_removal_job_id: REMOVE_JOB,
      },
      [`GET ${path}`]: () => removalJob('completed'),
      [`GET ${path}/events`]: () => sse([1, 'claimed', { attempt: 1 }], [2, 'completed']),
    })
    renderAt(PAGE)
    const card = await openDelete()
    expect(card).toHaveTextContent('Deleting it removes it there too.')
    fireEvent.click(within(card).getByRole('button', { name: 'Yes, delete' }))
    expect(await screen.findByRole('heading', { name: 'Class deleted' })).toBeInTheDocument()
    expect(await screen.findByText('The player has removed the class.')).toBeInTheDocument()
  })

  it('says when the removal could not be decided', async () => {
    const path = `/api/jobs/${REMOVE_JOB}`
    start(SENT, {}, {
      [`DELETE ${CLASS_PATH}`]: {
        deleted: true,
        mowl_workouts_kept: 0,
        note: 'x',
        player_removal_started: true,
        player_removal_job_id: REMOVE_JOB,
      },
      [`GET ${path}`]: () => removalJob('failed', { error_category: 'uncertain_write' }),
      [`GET ${path}/events`]: () => sse([1, 'claimed', { attempt: 1 }], [2, 'failed']),
    })
    renderAt(PAGE)
    const card = await openDelete()
    fireEvent.click(within(card).getByRole('button', { name: 'Yes, delete' }))
    expect(await screen.findByText(/We could not tell if the player removed it/)).toBeInTheDocument()
  })

  it('follows no job when the answer started none', async () => {
    start(SENT, {}, {
      [`DELETE ${CLASS_PATH}`]: {
        deleted: true,
        mowl_workouts_kept: 0,
        note: 'x',
        player_removal_started: false,
        player_removal_job_id: null,
      },
    })
    renderAt(PAGE)
    const card = await openDelete()
    fireEvent.click(within(card).getByRole('button', { name: 'Yes, delete' }))
    expect(await screen.findByRole('heading', { name: 'Class deleted' })).toBeInTheDocument()
    expect(screen.queryByTestId('player-removal')).toBeNull()
  })
})
