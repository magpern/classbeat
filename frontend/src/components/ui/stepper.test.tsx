import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Stepper } from './stepper'

function Harness({ start = '2', min = 0, max = 4, hint }: { start?: string; min?: number; max?: number; hint?: string }) {
  const [value, setValue] = useState(start)
  return <Stepper label="Climbs" fewer="Fewer climbs" more="More climbs" hint={hint} value={value} min={min} max={max} onChange={setValue} />
}

describe('Stepper', () => {
  it('has a labelled number input and two named buttons', () => {
    render(<Harness />)
    expect(screen.getByRole('spinbutton', { name: 'Climbs' })).toHaveValue(2)
    expect(screen.getByRole('button', { name: 'Fewer climbs' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'More climbs' })).toBeInTheDocument()
  })

  it('shows the hint beside the label, and nothing without one', () => {
    const { unmount } = render(<Harness hint="0 to 4" />)
    expect(screen.getByText('0 to 4')).toHaveClass('whitespace-nowrap')
    unmount()
    render(<Harness />)
    expect(screen.queryByText('0 to 4')).toBeNull()
  })

  it('steps with the buttons by mouse and by keyboard', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'More climbs' }))
    expect(screen.getByRole('spinbutton')).toHaveValue(3)
    screen.getByRole('button', { name: 'Fewer climbs' }).focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.keyboard(' ')
    expect(screen.getByRole('spinbutton')).toHaveValue(1)
  })

  it('stops at the limits and disables the button there', async () => {
    render(<Harness start="3" />)
    const more = screen.getByRole('button', { name: 'More climbs' })
    await userEvent.click(more)
    expect(screen.getByRole('spinbutton')).toHaveValue(4)
    expect(more).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Fewer climbs' })).toBeEnabled()
  })

  it('disables Fewer at the minimum', () => {
    render(<Harness start="0" />)
    expect(screen.getByRole('button', { name: 'Fewer climbs' })).toBeDisabled()
  })

  it('keeps typed text and starts from the minimum when the text is not a number', async () => {
    render(<Harness start="" min={1} />)
    expect(screen.getByRole('button', { name: 'Fewer climbs' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'More climbs' }))
    expect(screen.getByRole('spinbutton')).toHaveValue(2)
    await userEvent.clear(screen.getByRole('spinbutton'))
    await userEvent.type(screen.getByRole('spinbutton'), '3')
    expect(screen.getByRole('spinbutton')).toHaveValue(3)
  })

  it.each(['dark', 'light'])('is styled with tokens only, so it follows the %s theme', (theme) => {
    const { container } = render(
      <div data-theme={theme}>
        <Harness />
      </div>,
    )
    const button = screen.getByRole('button', { name: 'More climbs' })
    expect(button.className).toContain('bg-raised')
    expect(button.className).toContain('border-line')
    expect(button.className).toContain('focus-visible:outline-brand')
    expect(container.innerHTML).not.toMatch(/style=|#[0-9a-f]{3,8}\b|rgb\(/i)
  })
})
