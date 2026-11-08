import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fakeApi } from '../test/fakeApi'
import { renderAt } from '../test/render'

const PASSKEYS = [
  { id: 'p1', label: 'Work', created_at: '2026-01-01T00:00:00Z', last_used_at: null },
  { id: 'p2', label: 'Phone', created_at: '2026-01-02T00:00:00Z', last_used_at: null },
  { id: 'p3', label: 'Tablet', created_at: '2026-01-03T00:00:00Z', last_used_at: null },
]

async function resetWith(passkeys: typeof PASSKEYS, remove: string[]) {
  const api = fakeApi({
    'POST /api/auth/password/reset/info': { methods: [], passkeys },
    'POST /api/auth/password/reset/complete': { status: 'ok' },
  })
  renderAt('/reset#token=reset-token')
  for (const name of remove) fireEvent.click(await screen.findByLabelText(`Remove ${name}`))
  fireEvent.change(await screen.findByLabelText('New password'), { target: { value: 'a brand new passphrase' } })
  fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
  expect(await screen.findByText('Password changed')).toBeInTheDocument()
  return api.to('POST', '/api/auth/password/reset/complete')[0]?.body
}

describe('reset page passkeys', () => {
  it('sends every unticked passkey in keep_passkeys', async () => {
    const body = await resetWith(PASSKEYS, ['Work'])
    expect(body).toMatchObject({ keep_passkeys: ['p2', 'p3'], keep_methods: [] })
  })

  it('sends an empty keep list when every passkey is ticked', async () => {
    const body = await resetWith(PASSKEYS, ['Work', 'Phone', 'Tablet'])
    expect(body).toMatchObject({ keep_passkeys: [] })
  })

  it('keeps all passkeys when none is ticked', async () => {
    const body = await resetWith(PASSKEYS, [])
    expect(body).toMatchObject({ keep_passkeys: ['p1', 'p2', 'p3'] })
  })
})
