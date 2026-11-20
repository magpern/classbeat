import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AccountHeader } from './AccountHeader'

const LONG =
  'magnus.pernemark.the.second.of.his.name-and.family_tree@subdomain.mail-service.example.org'
const UNBROKEN = 'averyveryverylongunbrokenlocalpartwithnoseparatorsatall1234567890@example.test'

describe('AccountHeader', () => {
  it('shows the address as the one h1, once, with a stable test id', () => {
    render(<AccountHeader email="ann@example.test" />)
    const h1 = screen.getByRole('heading', { level: 1, name: 'ann@example.test' })
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(h1).toContainElement(screen.getByTestId('account-email'))
    expect(screen.getByTestId('account-email')).toHaveTextContent('ann@example.test')
    // No second line with the address, and no name cut from it.
    expect(screen.getAllByText('ann@example.test')).toHaveLength(1)
    expect(screen.queryByText('ann')).toBeNull()
    expect(screen.getByText('Your account').tagName).toBe('P')
  })

  it('shows the first letter of the address in the avatar, and copes with no "@"', () => {
    const { container, unmount } = render(<AccountHeader email="zed@example.test" />)
    expect(container.querySelector('[aria-hidden="true"] span')).toHaveTextContent('Z')
    unmount()
    render(<AccountHeader email="noat" />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('noat')
  })

  it('keeps the address on one line on a phone and cuts a long one, with the full address on hover', () => {
    render(<AccountHeader email={LONG} />)
    const h1 = screen.getByRole('heading', { level: 1 })
    // `truncate` is nowrap, hidden overflow and an ellipsis; from `sm` the heading wraps again.
    expect(h1).toHaveClass('truncate', 'sm:overflow-visible', 'sm:whitespace-normal', 'sm:text-clip')
    // The cut text is still all in the DOM (accessible name) and in the title attribute.
    expect(h1).toHaveAccessibleName(LONG)
    expect(screen.getByTestId('account-email')).toHaveAttribute('title', LONG)
    // A smaller title for a phone, and the card's own text sizes from `sm`.
    expect(h1).toHaveClass('text-sm', 'sm:text-2xl')
  })

  it('uses the largest title from sm up for a short address', () => {
    render(<AccountHeader email="ann@example.test" />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveClass('text-sm', 'sm:text-3xl')
  })

  it('wraps only at "@", ".", "-" and "_" (from sm up), never inside a word', () => {
    const { container } = render(<AccountHeader email={LONG} />)
    const span = screen.getByTestId('account-email')
    expect(span.textContent).toBe(LONG)
    const breaks = [...container.querySelectorAll('wbr')]
    expect(breaks.length).toBe(LONG.split(/[@._-]/).length - 1)
    for (const wbr of breaks) {
      const before = wbr.previousSibling?.textContent ?? ''
      const after = wbr.nextSibling?.textContent ?? ''
      expect(/[._-]$/.test(before) || after.startsWith('@')).toBe(true)
      // Not on a phone: Chrome would break at a `<wbr>` even in a nowrap line.
      expect(wbr).toHaveClass('hidden', 'sm:inline')
    }
  })

  it('stays one line with a very long address without a separator', () => {
    render(<AccountHeader email={UNBROKEN} />)
    const h1 = screen.getByRole('heading', { level: 1 })
    expect(h1).toHaveClass('truncate', 'hyphens-none')
    expect(h1).toHaveAccessibleName(UNBROKEN)
    expect(screen.getByTestId('account-email')).toHaveAttribute('title', UNBROKEN)
  })
})
