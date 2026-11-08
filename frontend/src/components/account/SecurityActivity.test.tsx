import { fireEvent, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ACTIVITY_KINDS } from '../../api/activity'
import { en, type CatalogueKey } from '../../i18n/en'
import { SIGNED_IN, fakeApi, json } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

const ACCOUNT = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
  'GET /api/auth/sessions': [],
}

function event(id: string, kind: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    kind,
    at: '2026-11-01T12:00:00Z',
    ip: '198.51.100.7',
    provider: null,
    other_session: false,
    ...extra,
  }
}

async function activityList() {
  return within(await screen.findByTestId('security-activity'))
}

describe('security activity', () => {
  it('lists the events with their address, the provider and a sign-out from another device', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/auth/events': {
        events: [
          event('e1', 'sign_in', { provider: 'github' }),
          event('e2', 'sign_out', { other_session: true }),
          event('e3', 'password_changed', { ip: null }),
          event('e4', 'some_future_kind'),
        ],
        next_before: null,
      },
    })
    renderAt('/account#security')
    const list = await activityList()
    const items = list.getAllByRole('listitem')
    expect(items).toHaveLength(3) // the unknown kind is dropped
    expect(items[0]).toHaveTextContent('Signed in with GitHub')
    expect(items[0]).toHaveTextContent('From 198.51.100.7')
    expect(items[1]).toHaveTextContent('A device was signed out from another device')
    expect(items[2]).toHaveTextContent('Password changed')
    expect(items[2]).not.toHaveTextContent('From')
    expect(screen.queryByRole('button', { name: 'Show older' })).toBeNull()
  })

  it('words passkey events, and the passkey method of sign-in and re-authentication', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/auth/events': {
        events: [
          event('e1', 'sign_in', { passkey: true }),
          event('e2', 'sign_in', { passkey: false }),
          event('e3', 'reauth', { passkey: true }),
          event('e4', 'reauth_failed', { passkey: true }),
          event('e5', 'sign_in_failed', { passkey: true }),
          event('e6', 'passkey_added'),
          event('e7', 'passkey_counter_regressed'),
        ],
        next_before: null,
      },
    })
    renderAt('/account#security')
    const list = await activityList()
    expect(await list.findByText('Signed in with a passkey')).toBeInTheDocument()
    expect(list.getByText('Signed in')).toBeInTheDocument()
    expect(list.getByText('Confirmed it was you with a passkey')).toBeInTheDocument()
    expect(list.getByText('Failed attempt to confirm it was you with a passkey')).toBeInTheDocument()
    expect(list.getByText('Failed sign-in attempt with a passkey')).toBeInTheDocument()
    expect(list.getByText('Passkey added')).toBeInTheDocument()
    expect(list.getByText('A passkey was used in a way that suggests it was copied')).toBeInTheDocument()
  })

  it('loads the next page with the cursor', async () => {
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/auth/events': { events: [event('e1', 'sign_in')], next_before: '123_abc' },
      'GET /api/auth/events?before=123_abc': () =>
        json({ events: [event('e2', 'sign_out_all')], next_before: null }),
    })
    renderAt('/account#security')
    await activityList()
    fireEvent.click(await screen.findByRole('button', { name: 'Show older' }))
    expect(await screen.findByText('Signed out everywhere')).toBeInTheDocument()
    expect((await activityList()).getAllByRole('listitem')).toHaveLength(2)
    expect(api.to('GET', '/api/auth/events?before=123_abc')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Show older' })).toBeNull()
  })

  it('says so when there is nothing, and when the list cannot be loaded', async () => {
    fakeApi(ACCOUNT)
    const first = renderAt('/account#security')
    expect(await screen.findByText('Nothing in the last 30 days.')).toBeInTheDocument()
    first.unmount()

    fakeApi({ ...ACCOUNT, 'GET /api/auth/events': () => json({ detail: 'boom' }, 500) })
    renderAt('/account#security')
    expect(
      await screen.findByText('The security activity could not be loaded. Reload the page to try again.'),
    ).toBeInTheDocument()
  })

  it('knows exactly the kinds the server lists, each worded in English', () => {
    const fixture = JSON.parse(
      readFileSync(
        join(import.meta.dirname, '../../../../backend/tests/fixtures/activity_kinds.json'),
        'utf8',
      ),
    ) as string[]
    expect([...ACTIVITY_KINDS].sort()).toEqual(fixture)
    for (const kind of ACTIVITY_KINDS) {
      expect(en[`activity.kind.${kind}` as CatalogueKey]).toBeTruthy()
    }
  })
})
