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
    expect(screen.getByRole('button', { name: 'Use my Swedebeat mix' })).toBeDisabled()
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

  it('offers No music alone when the soundless flag is on and nothing else is', () => {
    let chosen: string | null = null
    withRouter(
      <MusicChoice
        songs={{ kind: 'off' }}
        swedebeat={{ kind: 'off' }}
        soundless
        value="playlist"
        onChange={(m) => {
          chosen = m
        }}
      />,
    )
    const button = screen.getByRole('button', { name: 'No music' })
    expect(button.className).toContain('min-h-11')
    expect(button).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(button)
    expect(chosen).toBe('none')
  })

  it('says in plain words what No music means when it is chosen, and only then', () => {
    const { rerender } = withRouter(
      <MusicChoice songs={{ kind: 'off' }} swedebeat={{ kind: 'off' }} soundless value="none" onChange={() => undefined} />,
    )
    expect(screen.getByRole('button', { name: 'No music' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/without a playlist or songs/)).toBeInTheDocument()
    rerender(
      <RouterProvider>
        <MusicChoice songs={{ kind: 'off' }} swedebeat={{ kind: 'off' }} soundless value="playlist" onChange={() => undefined} />
      </RouterProvider>,
    )
    expect(screen.queryByText(/without a playlist or songs/)).toBeNull()
  })

  it('has no No music chip without the flag, with or without other choices', () => {
    withRouter(
      <MusicChoice
        songs={{ kind: 'ready', spotifyConnectionId: 's1' }}
        swedebeat={{ kind: 'ready' }}
        value="playlist"
        onChange={() => undefined}
      />,
    )
    expect(screen.queryByRole('button', { name: 'No music' })).toBeNull()
  })
})
