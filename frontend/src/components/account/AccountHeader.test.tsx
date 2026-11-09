import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AccountHeader } from './AccountHeader'

describe('AccountHeader', () => {
  it('always shows the address under the name, with a stable test id', () => {
    const { unmount } = render(<AccountHeader email="ann@example.test" />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('ann')
    expect(screen.getByTestId('account-email')).toHaveTextContent('ann@example.test')
    unmount()
    render(<AccountHeader email="noat" />)
    expect(screen.getByTestId('account-email')).toHaveTextContent('noat')
  })
})
