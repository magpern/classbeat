import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'

const IDENTITIES = { identities: [], has_password: true, available: [] }
const MFA_OFF = { totp_enabled: false, recovery_codes_left: 0 }

function account(usage: unknown) {
  return fakeApi({
    'GET /api/auth/session': SIGNED_IN,
    'GET /api/auth/identities': IDENTITIES,
    'GET /api/auth/mfa': MFA_OFF,
    'GET /api/usage': usage as object,
  })
}

describe('the usage panel on the account page', () => {
  it('shows classes designed and revisions, and no tokens, cost, models or billing caveats (#820)', async () => {
    account({
      month: '2026-10',
      total: {
        runs: 3,
        jobs: 2,
        input_tokens: 10000,
        output_tokens: 2500,
        cache_read_tokens: 500,
        cache_write_tokens: 0,
        cost_micros: 420000,
      },
      by_kind_model: [
        {
          job_kind: 'design_draft',
          model: 'claude-secret-model',
          runs: 3,
          jobs: 2,
          input_tokens: 10000,
          output_tokens: 2500,
          cache_read_tokens: 500,
          cache_write_tokens: 0,
          cost_micros: 420000,
        },
      ],
      classes_designed: 2,
      revisions: 5,
      notes: ['estimate_at_list_prices', 'sdk_retries', 'lost_lease_turn', 'failed_stream_turn'],
    })
    const { container } = renderAt('/account#plan')
    const facts = await screen.findByTestId('usage-facts')
    expect(within(facts).getByText('Classes designed').nextSibling).toHaveTextContent('2')
    expect(within(facts).getByText('Revisions').nextSibling).toHaveTextContent('5')
    expect(screen.getByText(/October 2026/)).toBeInTheDocument()
    const text = container.textContent
    for (const hidden of ['Tokens', 'Estimated cost', '$0.42', '13,000', '10,000', 'claude-secret-model']) {
      expect(text).not.toContain(hidden)
    }
    expect(text).not.toMatch(/provider|bill|list prices|retries/i)
    expect(screen.queryByTestId('usage-notes')).toBeNull()
    expect(container.querySelector('table')).toBeNull()
  })

  it('says so when usage cannot be loaded and offers to try again', async () => {
    account(json({}, 500))
    renderAt('/account#plan')
    expect(await screen.findByText('Your usage could not be loaded.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})
