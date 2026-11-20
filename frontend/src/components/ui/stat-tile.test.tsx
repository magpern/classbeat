import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatTile, StatTiles } from './stat-tile'

describe('StatTile', () => {
  it('is a dl with a dt label and a dd value', () => {
    render(
      <StatTiles aria-label="Figures">
        <StatTile label="Classes" value={7} data-testid="tile" />
      </StatTiles>,
    )
    const list = screen.getByLabelText('Figures')
    expect(list.tagName).toBe('DL')
    const label = screen.getByText('Classes')
    expect(label.tagName).toBe('DT')
    const value = screen.getByText('7')
    expect(value.tagName).toBe('DD')
    expect(value.className).toContain('tabular-nums')
    expect(screen.getByTestId('tile').className).toContain('bg-raised')
  })

  it('shows the sub line only when given', () => {
    const { rerender } = render(
      <dl>
        <StatTile label="L" value="1" />
      </dl>,
    )
    expect(screen.queryByText('left')).toBeNull()
    rerender(
      <dl>
        <StatTile label="L" value="1" sub="left" />
      </dl>,
    )
    expect(screen.getByText('left')).toBeTruthy()
  })

  it('has an outlined tone with a border on the surface colour', () => {
    render(
      <dl>
        <StatTile label="L" value="1" tone="outlined" data-testid="t" />
      </dl>,
    )
    const cls = screen.getByTestId('t').className
    expect(cls).toContain('border-line')
    expect(cls).toContain('bg-surface')
    expect(cls).not.toContain('bg-raised')
  })
})
