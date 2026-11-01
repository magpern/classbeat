import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { RuleHit } from '../../api/classes'
import { fakeApi, json, reason } from '../../test/fakeApi'
import { CLASS_ID } from '../../test/jobFixtures'
import { PatternNotes } from './PatternNotes'

const RULE_PATH = (rule: string) => `/api/classes/${CLASS_ID}/dismissed-rules/${rule}`

const WARMUP: RuleHit = {
  rule: 'warmup_short',
  severity: 'warn',
  metric: 'first_zone4plus_s',
  value: 240,
  bounds: [370, null],
  explainedByBrief: false,
}
const COOLDOWN: RuleHit = {
  rule: 'cooldown_long',
  severity: 'info',
  metric: 'cooldown_s',
  value: 300,
  bounds: [null, 188],
  explainedByBrief: false,
}
const EXPLAINED: RuleHit = { ...WARMUP, rule: 'hard_share_high', explainedByBrief: true }

function show(hits: RuleHit[], flagged = false) {
  const view = render(<PatternNotes classId={CLASS_ID} hits={hits} notesUseCurrentRules={flagged} />)
  const block = screen.queryByTestId('pattern-notes')
  if (block !== null) block.setAttribute('open', '')
  return view
}

describe('PatternNotes', () => {
  it('renders nothing without hits, even when the rules are flagged', () => {
    const { container } = show([], true)
    expect(container).toBeEmptyDOMElement()
  })

  it('is collapsed, and shows the copy, the value and no alert role', () => {
    render(<PatternNotes classId={CLASS_ID} hits={[WARMUP, COOLDOWN]} notesUseCurrentRules={false} />)
    expect(screen.getByTestId('pattern-notes')).not.toHaveAttribute('open')
    expect(screen.getByText(/Pattern notes/)).toBeInTheDocument()
    expect(screen.getByText(/Most classes of this kind ride a few minutes/)).toBeInTheDocument()
    expect(screen.getByText('This one: 4:00. Usual: 6:10 or more')).toBeInTheDocument()
    expect(screen.getByText('Worth a look.')).toBeInTheDocument()
    expect(screen.getByText('Good to know.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText('Notes use the current rules')).toBeNull()
  })

  it('shows at most five notes and skips rule ids it has no copy for', () => {
    const ids = [
      'warmup_short',
      'hard_start_late',
      'cooldown_short',
      'cooldown_long',
      'hard_share_low',
      'hard_share_high',
    ]
    show([{ ...WARMUP, rule: 'unknown_rule' }, ...ids.map((rule) => ({ ...WARMUP, rule }))])
    expect(screen.getAllByTestId('pattern-note')).toHaveLength(5)
  })

  it('renders an explained note muted with its reason', () => {
    show([EXPLAINED])
    const note = screen.getByTestId('pattern-note')
    expect(note).toHaveAttribute('data-explained', 'true')
    expect(note).toHaveClass('text-muted')
    expect(within(note).getByText('Matches your brief.')).toBeInTheDocument()
    expect(within(note).getByText(/Your brief asks for this/)).toBeInTheDocument()
  })

  it('shows the muted line when the notes use the current rules', () => {
    show([WARMUP], true)
    expect(screen.getByTestId('notes-current-rules')).toHaveTextContent('Notes use the current rules')
  })

  it('dismisses at once, moves focus to Show again, and undoes', async () => {
    const api = fakeApi({
      [`PUT ${RULE_PATH('warmup_short')}`]: { dismissed_rules: ['warmup_short'] },
      [`DELETE ${RULE_PATH('warmup_short')}`]: { dismissed_rules: [] },
    })
    const user = userEvent.setup()
    show([WARMUP, COOLDOWN])
    await user.click(screen.getByRole('button', { name: /^Dismiss: Most classes of this kind ride a few/ }))
    const again = screen.getByRole('button', { name: /^Show again: Most classes of this kind ride a few/ })
    expect(again).toHaveFocus()
    expect(screen.getAllByTestId('pattern-note')).toHaveLength(1)
    await waitFor(() => {
      expect(api.to('PUT', RULE_PATH('warmup_short'))).toHaveLength(1)
    })
    await user.click(again)
    expect(screen.getAllByTestId('pattern-note')).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: /^Dismiss:/ })[0]).toHaveFocus()
    await waitFor(() => {
      expect(api.to('DELETE', RULE_PATH('warmup_short'))).toHaveLength(1)
    })
  })

  it('puts the note back and says so when the dismissal fails', async () => {
    fakeApi({ [`PUT ${RULE_PATH('warmup_short')}`]: () => reason(500, 'server_error') })
    const user = userEvent.setup()
    show([WARMUP])
    await user.click(screen.getByRole('button', { name: /^Dismiss:/ }))
    expect(
      await screen.findByText('The note could not be dismissed. Try again in a moment.'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('pattern-notes-status')).toHaveAttribute('role', 'status')
    expect(screen.getByTestId('pattern-note')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Dismiss:/ })).toHaveFocus()
    })
  })

  it('keeps the note hidden and says so when showing it again fails', async () => {
    fakeApi({
      [`PUT ${RULE_PATH('warmup_short')}`]: json({ dismissed_rules: [] }),
      [`DELETE ${RULE_PATH('warmup_short')}`]: () => reason(500, 'server_error'),
    })
    const user = userEvent.setup()
    show([WARMUP])
    await user.click(screen.getByRole('button', { name: /^Dismiss:/ }))
    await user.click(screen.getByRole('button', { name: /^Show again:/ }))
    expect(
      await screen.findByText('The note could not be shown again. Try again in a moment.'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('pattern-note-hidden')).toBeInTheDocument()
  })

  it('gives every action a 44 px target', () => {
    show([WARMUP])
    for (const button of screen.getAllByRole('button')) expect(button.className).toContain('min-h-11')
  })
})
