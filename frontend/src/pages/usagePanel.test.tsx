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
  it('shows tokens, estimated cost, classes and revisions, and every note in words', async () => {
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
          model: 'claude-<b>x</b>',
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
    expect(within(facts).getByText('Tokens').nextSibling).toHaveTextContent('13,000')
    expect(within(facts).getByText('Estimated cost').nextSibling).toHaveTextContent('$0.42')
    expect(within(facts).getByText('Classes designed').nextSibling).toHaveTextContent('2')
    expect(within(facts).getByText('Revisions').nextSibling).toHaveTextContent('5')
    expect(screen.getByText(/October 2026/)).toBeInTheDocument()
    expect(screen.getByText(/slightly lower than your provider/)).toBeInTheDocument()
    const notes = screen.getByTestId('usage-notes')
    expect(within(notes).getAllByRole('listitem')).toHaveLength(4)
    expect(notes).toHaveTextContent(/estimate at the provider.s list prices/)
    expect(notes).toHaveTextContent(/retries quietly/)
    expect(notes).toHaveTextContent(/another worker/)
    expect(notes).toHaveTextContent(/begin to bill it returns no usage|began to bill it returns no usage/)
    expect(notes).not.toHaveTextContent('sdk_retries')
    // Server text is text, never HTML.
    expect(screen.getByText('claude-<b>x</b>')).toBeInTheDocument()
    expect(container.querySelector('b')).toBeNull()
  })

  it('words a code it does not know without showing the code', async () => {
    account({
      month: '2026-10',
      total: { runs: 0, jobs: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, cost_micros: 5000 },
      by_kind_model: [],
      classes_designed: 0,
      revisions: 0,
      notes: ['a_future_code'],
    })
    renderAt('/account#plan')
    const notes = await screen.findByTestId('usage-notes')
    expect(notes).toHaveTextContent(/another reason this figure may differ/)
    expect(notes).not.toHaveTextContent('a_future_code')
    expect(screen.getByText('under $0.01')).toBeInTheDocument()
  })

  it('says so when usage cannot be loaded and offers to try again', async () => {
    account(json({}, 500))
    renderAt('/account#plan')
    expect(await screen.findByText('Your usage could not be loaded.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})
