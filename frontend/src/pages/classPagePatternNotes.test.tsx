import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH, REV1, classRoutes, revisionBody } from '../test/classFixtures'
import { fakeApi } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

const HIT = {
  rule: 'warmup_short',
  severity: 'warn',
  metric: 'first_zone4plus_s',
  value: 240,
  bounds: [370, null],
  explained_by_brief: false,
}

function withRevision(extra: Record<string, unknown>) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ revisionBodies: { [REV1]: revisionBody(REV1, 1, undefined, extra) } }),
  }
}

describe('the class page pattern notes', () => {
  it('shows no block for a version without hits, whatever the flag says', async () => {
    fakeApi(withRevision({ rule_hits: [], notes_use_current_rules: true }))
    renderAt(`/classes/${CLASS_ID}`)
    expect(await screen.findByRole('heading', { name: /Tuesday climb/ })).toBeInTheDocument()
    expect(screen.queryByTestId('pattern-notes')).toBeNull()
    expect(screen.queryByText(/Notes use the current rules/)).toBeNull()
  })

  it('shows the block, the flag line, and dismisses without disabling anything', async () => {
    const api = fakeApi({
      ...withRevision({ rule_hits: [HIT], notes_use_current_rules: true }),
      [`PUT ${CLASS_PATH}/dismissed-rules/warmup_short`]: { dismissed_rules: ['warmup_short'] },
    })
    const user = userEvent.setup()
    renderAt(`/classes/${CLASS_ID}`)
    const block = await screen.findByTestId('pattern-notes')
    await user.click(block.querySelector('summary') as HTMLElement)
    expect(screen.getByText('Notes use the current rules')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
    for (const button of screen.getAllByRole('button')) {
      if (/^(Save|Approve)/.test(button.textContent)) expect(button).toBeEnabled()
    }
    await user.click(screen.getByRole('button', { name: /^Dismiss:/ }))
    await waitFor(() => {
      expect(api.to('PUT', `${CLASS_PATH}/dismissed-rules/warmup_short`)).toHaveLength(1)
    })
    expect(screen.getByRole('button', { name: /^Show again:/ })).toHaveFocus()
  })
})
