import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { contentHash } from '../lib/canonical'
import { CONTENT, REV1, classRoutes } from '../test/classFixtures'
import { fakeApi, json, reason } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES, jobBody, sse } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// Send to player on the class page (ADR 0034 section 6): every state, against the browser-facing
// player routes the backend serves. The press only opens the confirmation; the job is made by its
// button, and it names ids and a hash, never a document or a number.

const PAGE = `/classes/${CLASS_ID}`
const SEND_JOB = 'e2222222-2222-4222-8222-222222222222'
const OP = 'f2222222-2222-4222-8222-222222222222'
const PLAYER = '/api/connections/player'
const OPS = '/api/external-operations'

const status = (value: string, linkEnabled = true) => ({
  status: value,
  last_error: value === 'needs_reconnect' ? 'needs_reconnect' : null,
  connected_at: value === 'not_connected' ? null : '2026-11-10T10:00:00Z',
  link_enabled: linkEnabled,
})

function sendJob(state: string, extra: Record<string, unknown> = {}) {
  return jobBody(state, { id: SEND_JOB, kind: 'send_to_player', ...extra })
}

function start(player: Record<string, unknown>, more: Record<string, object | (() => unknown)> = {}, cls = {}) {
  return fakeApi({
    ...SESSION_ROUTES,
    ...classRoutes({ cls }),
    [`GET ${PLAYER}`]: player,
    ...more,
  })
}

const posts = (api: ReturnType<typeof fakeApi>) => api.to('POST', '/api/jobs')
const press = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Send to player' }))
  return screen.findByRole('group', { name: 'Send to classbeat-player' })
}
const confirm = (dialog: HTMLElement) => {
  fireEvent.click(within(dialog).getByRole('button', { name: 'Send to player' }))
}

/** A run that ends as `state`, with `category` for a failure. */
function ended(state: string, category: string | null = null) {
  const path = `/api/jobs/${SEND_JOB}`
  return {
    'POST /api/jobs': () => sendJob('queued'),
    [`GET ${path}`]: () => sendJob(state, { error_category: category }),
    [`GET ${path}/events`]: () => sse([1, 'claimed', { attempt: 1 }], [2, state === 'completed' ? 'completed' : 'failed']),
  }
}

describe('where Send to player is offered', () => {
  it('is not on the page while the player is off and nothing is connected', async () => {
    start(status('not_connected', false))
    renderAt(PAGE)
    await screen.findByTestId('class-header')
    expect(screen.queryByRole('button', { name: 'Send to player' })).toBeNull()
    expect(screen.queryByRole('link', { name: /classbeat-player/ })).toBeNull()
  })

  it('sends a class with no player connection to Connections instead of a button', async () => {
    start(status('not_connected'))
    renderAt(PAGE)
    const link = await screen.findByRole('link', { name: 'Connect classbeat-player to send this class' })
    expect(link).toHaveAttribute('href', '/en/account/player')
    expect(screen.queryByRole('button', { name: 'Send to player' })).toBeNull()
  })

  it('says reconnect when the link needs it', async () => {
    start(status('needs_reconnect'))
    renderAt(PAGE)
    expect(
      await screen.findByRole('link', { name: 'Reconnect classbeat-player to send this class' }),
    ).toHaveAttribute('href', '/en/account/player')
  })

  it('puts the button in the class header, beside Save to MOWL', async () => {
    start(status('connected'))
    renderAt(PAGE)
    const header = await screen.findByTestId('class-header')
    expect(await within(header).findByRole('button', { name: 'Send to player' })).toBeEnabled()
    expect(within(header).getByRole('button', { name: 'Save to MOWL' })).toBeInTheDocument()
  })

  it('still offers the button when the status cannot be read: the server refuses a send if it must', async () => {
    start(json({}, 500) as unknown as Record<string, unknown>)
    renderAt(PAGE)
    expect(await screen.findByRole('button', { name: 'Send to player' })).toBeEnabled()
  })
})

describe('the press', () => {
  it('sends nothing until the confirmation is answered, and says it replaces the player copy', async () => {
    const api = start(status('connected'))
    renderAt(PAGE)
    const dialog = await press()
    expect(dialog).toHaveTextContent(/Version 1 goes to your classbeat-player library as it is now/)
    expect(dialog).toHaveTextContent(/replaces the player’s copy, also any change you made there/)
    expect(posts(api)).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.queryByRole('group', { name: 'Send to classbeat-player' })).toBeNull()
    })
    expect(posts(api)).toHaveLength(0)
  })

  it('sends the class, the version and its hash only, with an idempotency key', async () => {
    const api = start(status('connected'), ended('completed'))
    renderAt(PAGE)
    confirm(await press())
    await waitFor(() => {
      expect(posts(api)).toHaveLength(1)
    })
    const call = posts(api)[0]!
    expect(call.body).toEqual({
      kind: 'send_to_player',
      input: { class_id: CLASS_ID, revision_id: REV1, content_hash: await contentHash(CONTENT) },
    })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(call.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })
})

describe('the states of a send', () => {
  it('sending: shows progress on the open job and disables the button', async () => {
    const path = `/api/jobs/${SEND_JOB}`
    start(
      status('connected'),
      {
        [`GET ${path}`]: () => sendJob('running'),
        [`GET ${path}/events`]: () => sse([1, 'claimed', { attempt: 1 }], [2, 'stage_started', { stage: 'send' }]),
      },
      { open_job_id: SEND_JOB, open_job: { id: SEND_JOB, kind: 'send_to_player', state: 'running' } },
    )
    renderAt(PAGE)
    await waitFor(() => {
      expect(screen.getByTestId('player-progress')).toHaveTextContent('Sending to classbeat-player…')
    })
    expect(screen.getByRole('button', { name: 'Send to player' })).toBeDisabled()
  })

  it('sent: says which version, and offers Send again', async () => {
    start(status('connected'), ended('completed'))
    renderAt(PAGE)
    confirm(await press())
    expect(await screen.findByText('Sent to classbeat-player: version 1.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send again' })).toBeEnabled()
  })

  it('player ahead: says it caught up and offers Send again, which asks first', async () => {
    const api = start(status('connected'), ended('failed', 'player_ahead'))
    renderAt(PAGE)
    confirm(await press())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The player already had a newer copy.')
    expect(alert).toHaveTextContent('press Send to player again')
    expect(screen.queryByText('player_ahead')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Send again' }))
    expect(await screen.findByRole('group', { name: 'Send to classbeat-player' })).toBeInTheDocument()
    expect(posts(api)).toHaveLength(1) // nothing is sent again by itself
  })

  it('changed on the player: says nothing was replaced, and a new press replaces the copy', async () => {
    const api = start(status('connected'), ended('failed', 'player_conflict'))
    renderAt(PAGE)
    confirm(await press())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This class changed on the player.')
    expect(alert).toHaveTextContent('Nothing was replaced.')
    fireEvent.click(screen.getByRole('button', { name: 'Send again' }))
    expect(await screen.findByRole('group', { name: 'Send to classbeat-player' })).toHaveTextContent(/replaces the player’s copy/)
    expect(posts(api)).toHaveLength(1)
  })

  it('reconnect: a refused key points to the account page, not to MOWL', async () => {
    start(status('connected'), ended('failed', 'not_connected'))
    renderAt(PAGE)
    confirm(await press())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The link to classbeat-player stopped working.')
    expect(alert).not.toHaveTextContent('MOWL')
    expect(within(alert).getByRole('link', { name: 'Reconnect it on your account page' })).toHaveAttribute(
      'href',
      '/en/account/player',
    )
  })

  it('failed: words any other category in plain text, without the code', async () => {
    start(status('connected'), ended('failed', 'upstream_unavailable'))
    renderAt(PAGE)
    confirm(await press())
    const alert = await screen.findByRole('alert')
    expect(alert).not.toHaveTextContent('upstream_unavailable')
    expect(screen.queryByRole('button', { name: 'Send again' })).toBeNull()
  })

  it('stopped: says nothing was sent', async () => {
    start(status('connected'), ended('cancelled'))
    renderAt(PAGE)
    confirm(await press())
    expect(await screen.findByText('Stopped before anything was sent.')).toBeInTheDocument()
  })

  it('uncertain: points to the box below, which holds Check again and Do it anyway', async () => {
    start(status('connected'), {
      ...ended('failed', 'uncertain_write'),
      [`GET ${OPS}`]: {
        operations: [
          {
            id: OP,
            kind: 'player_send',
            state: 'uncertain',
            job_id: SEND_JOB,
            resolved_by: null,
            class_id: CLASS_ID,
            actionable_at: null,
          },
        ],
      },
    })
    renderAt(PAGE)
    confirm(await press())
    expect(await screen.findByText(/could not tell whether the player took it/)).toBeInTheDocument()
    const panel = await screen.findByTestId('uncertain-player')
    expect(panel).toHaveTextContent('We could not confirm the player received your class')
    expect(within(panel).getByRole('button', { name: 'Check again' })).toBeEnabled()
    expect(within(panel).getByRole('button', { name: 'Do it anyway' })).toBeEnabled()
  })
})

describe('the undecided send', () => {
  const uncertain = (extra: Record<string, unknown> = {}) => ({
    [`GET ${OPS}`]: {
      operations: [
        { id: OP, kind: 'player_send', state: 'uncertain', job_id: SEND_JOB, resolved_by: null, class_id: CLASS_ID, actionable_at: null, ...extra },
        // Another class's, and a MOWL write of this one: neither belongs in this panel.
        { id: 'a9999999-9999-4999-8999-999999999999', kind: 'player_send', state: 'uncertain', job_id: SEND_JOB, resolved_by: null, class_id: 'b9999999-9999-4999-8999-999999999999', actionable_at: null },
      ],
    },
  })

  it('lists only this class send and sends nothing by itself', async () => {
    const api = start(status('connected'), uncertain())
    renderAt(PAGE)
    expect(await screen.findAllByTestId('uncertain-player')).toHaveLength(1)
    expect(posts(api)).toHaveLength(0)
  })

  it.each([
    ['succeeded', 'The player has it. Nothing more to do.'],
    ['failed', 'The player does not have this send. You can send again.'],
    ['uncertain', 'Still not sure. Try again in a moment.'],
  ])('Check again that finds %s says so', async (state, sentence) => {
    const api = start(status('connected'), {
      ...uncertain(),
      [`POST ${OPS}/${OP}/check-again`]: { id: OP, kind: 'player_send', state, resolved_by: 'system', class_id: CLASS_ID, actionable_at: null },
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    expect(api.to('POST', `${OPS}/${OP}/check-again`)).toHaveLength(1)
    expect(posts(api)).toHaveLength(0)
  })

  it('Do it anyway asks first, saying the next send replaces any copy', async () => {
    const api = start(status('connected'), {
      ...uncertain(),
      [`POST ${OPS}/${OP}/do-it-anyway`]: { id: OP, kind: 'player_send', state: 'failed', resolved_by: 'user', class_id: CLASS_ID, actionable_at: null },
    })
    renderAt(PAGE)
    fireEvent.click(await screen.findByRole('button', { name: 'Do it anyway' }))
    const confirmBox = screen.getByRole('group', { name: 'Confirm' })
    expect(confirmBox).toHaveTextContent('your next send replaces that copy. Nothing is duplicated.')
    expect(api.to('POST', `${OPS}/${OP}/do-it-anyway`)).toHaveLength(0)
    fireEvent.click(within(confirmBox).getByRole('button', { name: 'Yes, it was not sent' }))
    expect(await screen.findByText('Marked as not sent. You can send again.')).toBeInTheDocument()
    expect(posts(api)).toHaveLength(0)
  })
})

describe('refused at the press', () => {
  const refused = (why: string) => ({ 'POST /api/jobs': () => reason(409, why) })

  it.each([
    ['not_connected', /classbeat-player is not connected\. Connect it on your account page first\./],
    ['unresolved_write', /earlier send to the player is still undecided/],
    ['class_busy', /busy with another task/],
    ['hash_mismatch', /not the one this page showed/],
    ['no_songs', /music but no song list the player can play/],
    ['zone_out_of_range', /cannot show this class as it is/],
    ['song_gap', /cannot show this class as it is/],
  ])('words %s in plain text', async (why, sentence) => {
    start(status('connected'), refused(why))
    renderAt(PAGE)
    confirm(await press())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(sentence)
    expect(alert).not.toHaveTextContent(why)
  })

  it('reads the undecided list again after unresolved_write', async () => {
    const api = start(status('connected'), refused('unresolved_write'))
    renderAt(PAGE)
    confirm(await press())
    await screen.findByRole('alert')
    await waitFor(() => {
      expect(api.to('GET', OPS).length).toBeGreaterThanOrEqual(2)
    })
  })
})
