import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it } from 'vitest'
import { RouterProvider } from '../../router/Router'
import { MusicChoice } from './MusicChoice'

function withRouter(ui: ReactNode) {
  return render(<RouterProvider>{ui}</RouterProvider>)
}

describe('MusicChoice', () => {
  it('shows nothing when song picking and Swedebeat mixes are both off', () => {
    const { container } = withRouter(
      <MusicChoice
        songs={{ kind: 'off' }}
        swedebeat={{ kind: 'off' }}
        value="playlist"
        onChange={() => undefined}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('offers a Swedebeat mix chip when the flag is on and links to MOWL without it', () => {
    withRouter(
      <MusicChoice
        songs={{ kind: 'off' }}
        swedebeat={{ kind: 'needs_mowl' }}
        value="playlist"
        onChange={() => undefined}
      />,
    )
    expect(screen.getByRole('button', { name: 'Use my Swedebeat mix' })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Connect MOWL on your account page' })).toHaveAttribute('href', '/account')
  })

  it('shows a plain sentence when the mix list failed', () => {
    withRouter(
      <MusicChoice
        songs={{ kind: 'off' }}
        swedebeat={{ kind: 'failed', message: 'MOWL could not be reached. Try again in a few minutes.' }}
        value="playlist"
        onChange={() => undefined}
      />,
    )
    expect(screen.getByText('MOWL could not be reached. Try again in a few minutes.')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('upstream_unavailable')
  })

  it('reports pressed state and gives every chip a 44 px target', () => {
    withRouter(
      <MusicChoice
        songs={{ kind: 'ready', spotifyConnectionId: 's1' }}
        swedebeat={{ kind: 'ready' }}
        value="swedebeat_mix"
        onChange={() => undefined}
      />,
    )
    for (const button of screen.getAllByRole('button')) {
      expect(button.className).toContain('min-h-11')
    }
    expect(screen.getByRole('button', { name: 'Use my Swedebeat mix' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Use my playlist' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('calls onChange when a choice is picked', () => {
    let chosen: string | null = null
    withRouter(
      <MusicChoice
        songs={{ kind: 'off' }}
        swedebeat={{ kind: 'ready' }}
        value="playlist"
        onChange={(m) => {
          chosen = m
        }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Use my Swedebeat mix' }))
    expect(chosen).toBe('swedebeat_mix')
  })

  it('names the music group for assistive tech', () => {
    withRouter(
      <MusicChoice
        songs={{ kind: 'off' }}
        swedebeat={{ kind: 'ready' }}
        value="playlist"
        onChange={() => undefined}
      />,
    )
    expect(screen.getByRole('group', { name: 'Music' })).toBeInTheDocument()
  })
})
