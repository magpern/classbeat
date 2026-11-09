import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Button } from './button'
import { Chip } from './chip'
import { ListRow } from './list-row'

describe('ListRow', () => {
  it('is a list item with title, badge, description and one action', () => {
    render(
      <ul>
        <ListRow
          data-testid="row"
          leading={<span aria-hidden="true">M</span>}
          title="MOWL"
          badge={<Chip>Optional</Chip>}
          description="Connected as anna"
          action={<Button variant="secondary">Disconnect</Button>}
        />
      </ul>,
    )
    const row = screen.getByRole('listitem')
    expect(row).toHaveAttribute('data-testid', 'row')
    expect(within(row).getByText('MOWL')).toBeInTheDocument()
    expect(within(row).getByText('Optional')).toBeInTheDocument()
    expect(within(row).getByText('Connected as anna')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Disconnect' })).toBeInTheDocument()
  })

  it('renders only what it is given', () => {
    render(
      <ul>
        <ListRow title="Spotify" />
      </ul>,
    )
    const row = screen.getByRole('listitem')
    expect(row).toHaveTextContent('Spotify')
    expect(within(row).queryByRole('button')).toBeNull()
  })

  it('keeps a long title from pushing the action out', () => {
    render(
      <ul>
        <ListRow title="A very long name" description="and a long line" action={<Button>Connect</Button>} />
      </ul>,
    )
    expect(screen.getByText('A very long name').parentElement).toHaveClass('min-w-0', 'flex-1')
  })

  it.each(['dark', 'light'])('is styled with tokens only, so it follows the %s theme', (theme) => {
    const { container } = render(
      <ul data-theme={theme}>
        <ListRow title="MOWL" description="Not connected" />
      </ul>,
    )
    expect(screen.getByText('MOWL').className).toContain('text-text')
    expect(screen.getByText('Not connected').className).toContain('text-muted')
    expect(container.innerHTML).not.toMatch(/style=|#[0-9a-f]{3,8}\b|rgb\(/i)
  })
})
