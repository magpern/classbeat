import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { InterpretationItem } from '../../api/interpretationItems'
import { notAskedOf } from '../../lib/notAsked'
import { InterpretationNote } from '../classes/InterpretationNote'
import { ApproveBar } from './ApproveBar'

// Issue #1494: the approve bar lists the preview's Not as asked items, and the reading shows the shape.

const OFF: InterpretationItem = {
  path: 'shape.2',
  origin: 'not_as_asked',
  reasonCode: 'shape_off',
  value: { kind: 'efforts', count: { min: 7, max: 7 }, found: 5 },
  quote: null,
}
const BAR = { holding: false, checking: false, checkFailed: false, onRetryCheck: () => undefined, busy: false, error: null, onApprove: () => undefined }

describe('Not as asked before Approve', () => {
  it('shows a shape_off item above the Approve button', () => {
    render(<ApproveBar {...BAR} notAsked={notAskedOf([OFF, { ...OFF, path: 'length_min', origin: 'explicit' }])} />)
    const box = screen.getByTestId('not-asked-before')
    expect(box.textContent).toContain('Before you approve, note what is not as you asked:')
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Section 3: you asked for 7 efforts; this version has 5',
    ])
    expect(screen.getByRole('button', { name: 'Approve and save' })).toBeEnabled()
  })

  it('shows nothing extra when the version is as asked', () => {
    render(<ApproveBar {...BAR} />)
    expect(screen.queryByTestId('not-asked-before')).toBeNull()
  })

  it('lists an item once when two sources hold it', () => {
    expect(notAskedOf([OFF], [OFF], null)).toHaveLength(1)
  })
})

describe('the reading card part for the shape', () => {
  it('shows the sections with the instructor\'s words and what classbeat adds', () => {
    render(
      <InterpretationNote
        items={[
          { path: 'shape.0', origin: 'explicit', reasonCode: null, value: { kind: 'warmup', trend: 'rising' }, quote: 'starts off slow' },
          { path: 'shape.1', origin: 'explicit', reasonCode: null, value: { kind: 'efforts', count: { min: 5, max: 7 }, length: 'short', load: 'heavy' }, quote: null },
        ]}
      />,
    )
    expect(screen.getByRole('region', { name: 'Your ride, part by part' }).textContent).toContain('1. Warm-up, building up')
    expect(screen.getByText('2. 5 to 7 short, heavy efforts')).toBeTruthy()
    expect(screen.getByText('Your words: starts off slow')).toBeTruthy()
    expect(screen.getByTestId('shape-notes').textContent).toContain('classbeat picks the number within your ranges')
    expect(screen.getByTestId('shape-notes').textContent).toContain('classbeat adds a cool-down.')
  })
})
