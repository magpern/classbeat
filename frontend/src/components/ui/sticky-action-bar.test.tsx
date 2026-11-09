import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './button'
import { StickyActionBar } from './sticky-action-bar'

describe('StickyActionBar', () => {
  it('is a named region that holds the primary action', async () => {
    const onClick = vi.fn()
    render(
      <StickyActionBar label="Class actions">
        <Button onClick={onClick}>Approve songs</Button>
      </StickyActionBar>,
    )
    const region = screen.getByRole('region', { name: 'Class actions' })
    expect(region).toContainElement(screen.getByRole('button', { name: 'Approve songs' }))
    await userEvent.click(screen.getByRole('button', { name: 'Approve songs' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('is a bottom bar on a phone and a sticky card in the right column on a desktop', () => {
    render(<StickyActionBar label="Actions">x</StickyActionBar>)
    const { className } = screen.getByRole('region', { name: 'Actions' })
    for (const c of ['max-lg:fixed', 'max-lg:inset-x-0', 'max-lg:bottom-0', 'lg:sticky', 'lg:top-sticky']) {
      expect(className).toContain(c)
    }
  })

  it('keeps room under the page on a phone, hidden from assistive technology', () => {
    const { container } = render(<StickyActionBar label="Actions">x</StickyActionBar>)
    const spacer = container.firstElementChild
    expect(spacer).toHaveAttribute('aria-hidden', 'true')
    expect(spacer).toHaveClass('h-bar', 'lg:hidden')
  })

  it('pads the bottom of the phone bar by the safe-area inset, from a token and not an inline style', () => {
    const { container } = render(<StickyActionBar label="Actions">x</StickyActionBar>)
    const bar = screen.getByRole('region', { name: 'Actions' })
    expect(bar.className).toContain('max-lg:pb-bar-pad')
    expect(bar.className).not.toContain('env(')
    expect(container.innerHTML).not.toContain('style=')
  })

  it('lets the caller add classes', () => {
    render(
      <StickyActionBar label="Actions" className="gap-6">
        x
      </StickyActionBar>,
    )
    expect(screen.getByRole('region', { name: 'Actions' })).toHaveClass('gap-6', 'bg-surface')
  })

  it.each(['dark', 'light'])('is styled with tokens only, so it follows the %s theme', (theme) => {
    const { container } = render(
      <div data-theme={theme}>
        <StickyActionBar label="Actions">x</StickyActionBar>
      </div>,
    )
    const { className } = screen.getByRole('region', { name: 'Actions' })
    expect(className).toContain('bg-surface')
    expect(className).toContain('border-line')
    expect(container.innerHTML).not.toMatch(/style=|#[0-9a-f]{3,8}\b|rgb\(/i)
  })
})
