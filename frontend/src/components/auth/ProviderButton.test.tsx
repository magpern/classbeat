import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProviderButton } from './ProviderButton'

describe('ProviderButton', () => {
  it.each(['google', 'microsoft', 'github'])('%s: the visible text is the name, the logo is decoration', (provider) => {
    const { container } = render(<ProviderButton provider={provider}>Continue with {provider}</ProviderButton>)
    const button = screen.getByRole('button', { name: `Continue with ${provider}` })
    const logo = container.querySelector('svg')
    expect(logo).toHaveAttribute('aria-hidden', 'true')
    expect(button).toContainElement(logo)
    expect(button).toHaveAttribute('type', 'button')
  })

  it('Google and Microsoft carry their own button colours, from tokens', () => {
    render(
      <>
        <ProviderButton provider="google">Continue with Google</ProviderButton>
        <ProviderButton provider="microsoft">Continue with Microsoft</ProviderButton>
      </>,
    )
    expect(screen.getByRole('button', { name: 'Continue with Google' })).toHaveClass('bg-google-bg', 'text-google-text')
    expect(screen.getByRole('button', { name: 'Continue with Microsoft' })).toHaveClass('bg-microsoft-bg', 'text-microsoft-text')
  })

  it('an unknown provider is a plain button without a logo', () => {
    const { container } = render(<ProviderButton provider="other">Continue with Other</ProviderButton>)
    expect(container.querySelector('svg')).toBeNull()
  })

  it('clicks and disables like a button', () => {
    const onClick = vi.fn()
    const { rerender } = render(<ProviderButton provider="google" onClick={onClick}>Go</ProviderButton>)
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))
    expect(onClick).toHaveBeenCalledTimes(1)
    rerender(<ProviderButton provider="google" disabled onClick={onClick}>Go</ProviderButton>)
    fireEvent.click(screen.getByRole('button', { name: 'Go' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
