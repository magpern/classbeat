import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MixTiles } from './MixTiles'

const MIXES = [
  { mixId: 'mix-a', name: 'Sunday climb', durationSeconds: 1830, analysed: true },
  { mixId: 'mix-b', name: 'Warm up', durationSeconds: 600, analysed: false },
]

describe('MixTiles', () => {
  it('points at the Swedebeat app when MOWL lists no mixes', () => {
    render(<MixTiles mixes={[]} value="" onChange={() => undefined} />)
    expect(screen.getByText(/Create a mix in the Swedebeat app/)).toBeInTheDocument()
    expect(screen.queryByRole('radiogroup')).toBeNull()
  })

  it('lists mixes as one radio group with length and the analysed badge', () => {
    render(<MixTiles mixes={MIXES} value="" onChange={() => undefined} />)
    const group = screen.getByRole('radiogroup', { name: 'Swedebeat mix' })
    expect(group.querySelectorAll('input[type="radio"]')).toHaveLength(2)
    expect(within(group).getByText('Analysed before')).toBeInTheDocument()
    expect(within(group).getByText('31 min')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Sunday climb/ })).toBeInTheDocument()
  })

  it('renders server names as text', () => {
    render(
      <MixTiles
        mixes={[{ mixId: 'x', name: 'Hills <script>x</script>', durationSeconds: null, analysed: false }]}
        value=""
        onChange={() => undefined}
      />,
    )
    expect(screen.getByRole('radio', { name: 'Hills <script>x</script>' })).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
  })

  it('gives every tile a 44 px minimum height', () => {
    render(<MixTiles mixes={MIXES} value="" onChange={() => undefined} />)
    for (const label of screen.getAllByRole('radio').map((input) => input.closest('label')).filter(Boolean)) {
      expect(label?.className).toContain('min-h-11')
    }
  })

  it('calls onChange with the mix id', () => {
    let picked = ''
    render(<MixTiles mixes={MIXES} value="" onChange={(id) => { picked = id }} />)
    fireEvent.click(screen.getByRole('radio', { name: /Warm up/ }))
    expect(picked).toBe('mix-b')
  })
})
