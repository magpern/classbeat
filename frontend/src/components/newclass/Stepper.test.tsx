import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Stepper } from './Stepper'

describe('Stepper', () => {
  it('keeps the range beside the label so it never wraps beside the buttons', () => {
    render(<Stepper label="Climbs" fewer="Fewer climbs" more="More climbs" value="2" min={0} max={10} onChange={() => undefined} />)
    const range = screen.getByText('0 to 10')
    expect(range).toHaveClass('whitespace-nowrap')
    expect(range.parentElement).toContainElement(screen.getByText('Climbs'))
  })
})
