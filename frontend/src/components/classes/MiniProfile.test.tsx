import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MiniProfile } from './MiniProfile'

describe('MiniProfile', () => {
  it('draws one bar per interval, coloured by zone', () => {
    const { container } = render(
      <MiniProfile
        profile={[
          [2, 60],
          [4, 120],
        ]}
      />,
    )
    expect(screen.getByRole('img')).toBeInTheDocument()
    expect(container.querySelectorAll('rect')).toHaveLength(2)
  })

  it('shows a dashed placeholder, not an empty stage, when there is no profile yet', () => {
    const { container } = render(<MiniProfile profile={[]} />)
    const placeholder = screen.getByRole('img')
    expect(placeholder).toHaveClass('border-dashed')
    expect(container.querySelector('svg')).toBeNull()
  })
})
