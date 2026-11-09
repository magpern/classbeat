import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { SegmentedControl, type SegmentedOption } from './segmented-control'

const OPTIONS: SegmentedOption<'easy' | 'steady' | 'hard'>[] = [
  { value: 'easy', label: 'Easy' },
  { value: 'steady', label: 'Steady' },
  { value: 'hard', label: 'Hard' },
]

function Harness({ options = OPTIONS, start = 'easy' }: { options?: SegmentedOption<'easy' | 'steady' | 'hard'>[]; start?: 'easy' | 'steady' | 'hard' }) {
  const [value, setValue] = useState(start)
  return <SegmentedControl aria-label="Effort" options={options} value={value} onValueChange={setValue} />
}

describe('SegmentedControl', () => {
  it('is a named radio group with one checked radio', () => {
    render(<Harness />)
    expect(screen.getByRole('radiogroup', { name: 'Effort' })).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(screen.getByRole('radio', { name: 'Easy' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Steady' })).not.toBeChecked()
  })

  it('can be named by another element', () => {
    render(
      <>
        <span id="effort-label">Effort</span>
        <SegmentedControl aria-labelledby="effort-label" options={OPTIONS} value="easy" onValueChange={() => undefined} />
      </>,
    )
    expect(screen.getByRole('radiogroup', { name: 'Effort' })).toBeInTheDocument()
  })

  it('selects on click and reports the value', async () => {
    const onValueChange = vi.fn()
    render(<SegmentedControl aria-label="Effort" options={OPTIONS} value="easy" onValueChange={onValueChange} />)
    await userEvent.click(screen.getByRole('radio', { name: 'Hard' }))
    expect(onValueChange).toHaveBeenCalledWith('hard')
  })

  it('has one tab stop, on the selected option', async () => {
    render(
      <>
        <button type="button">Before</button>
        <Harness start="steady" />
        <button type="button">After</button>
      </>,
    )
    expect(screen.getByRole('radio', { name: 'Steady' })).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('radio', { name: 'Easy' })).toHaveAttribute('tabindex', '-1')
    await userEvent.click(screen.getByRole('button', { name: 'Before' }))
    await userEvent.tab()
    expect(screen.getByRole('radio', { name: 'Steady' })).toHaveFocus()
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'After' })).toHaveFocus()
  })

  it('moves and selects with the arrow keys, wrapping at the ends', async () => {
    render(<Harness />)
    screen.getByRole('radio', { name: 'Easy' }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('radio', { name: 'Steady' })).toHaveFocus()
    expect(screen.getByRole('radio', { name: 'Steady' })).toBeChecked()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(screen.getByRole('radio', { name: 'Easy' })).toBeChecked()
    await userEvent.keyboard('{ArrowLeft}')
    expect(screen.getByRole('radio', { name: 'Hard' })).toHaveFocus()
    expect(screen.getByRole('radio', { name: 'Hard' })).toBeChecked()
    await userEvent.keyboard('{ArrowUp}')
    expect(screen.getByRole('radio', { name: 'Steady' })).toBeChecked()
  })

  it('jumps with Home and End', async () => {
    render(<Harness start="steady" />)
    screen.getByRole('radio', { name: 'Steady' }).focus()
    await userEvent.keyboard('{End}')
    expect(screen.getByRole('radio', { name: 'Hard' })).toBeChecked()
    await userEvent.keyboard('{Home}')
    expect(screen.getByRole('radio', { name: 'Easy' })).toBeChecked()
  })

  it('skips a disabled option and cannot select it', async () => {
    const options = OPTIONS.map((o) => (o.value === 'steady' ? { ...o, disabled: true } : o))
    render(<Harness options={options} />)
    expect(screen.getByRole('radio', { name: 'Steady' })).toBeDisabled()
    screen.getByRole('radio', { name: 'Easy' }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('radio', { name: 'Hard' })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: 'Steady' }))
    expect(screen.getByRole('radio', { name: 'Hard' })).toBeChecked()
  })

  it('leaves other keys alone', async () => {
    const onValueChange = vi.fn()
    render(<SegmentedControl aria-label="Effort" options={OPTIONS} value="easy" onValueChange={onValueChange} />)
    screen.getByRole('radio', { name: 'Easy' }).focus()
    await userEvent.keyboard('a{Tab}')
    expect(onValueChange).not.toHaveBeenCalled()
  })

  it('makes the first option the tab stop when nothing valid is selected', () => {
    render(<SegmentedControl aria-label="Effort" options={OPTIONS} value={'' as 'easy'} onValueChange={() => undefined} />)
    expect(screen.getByRole('radio', { name: 'Easy' })).toHaveAttribute('tabindex', '0')
    expect(screen.queryAllByRole('radio', { checked: true })).toHaveLength(0)
  })

  it.each(['dark', 'light'])('is styled with tokens only, so it follows the %s theme', (theme) => {
    render(
      <div data-theme={theme}>
        <Harness />
      </div>,
    )
    const group = screen.getByRole('radiogroup')
    expect(group.className).toContain('bg-raised')
    const radio = screen.getByRole('radio', { name: 'Easy' })
    expect(radio.className).toContain('aria-checked:bg-brand')
    expect(radio.className).toContain('aria-checked:text-on-brand')
    expect(radio.className).toContain('focus-visible:outline-brand')
    expect(group.outerHTML).not.toMatch(/style=|#[0-9a-f]{3,8}\b|rgb\(/i)
  })
})
