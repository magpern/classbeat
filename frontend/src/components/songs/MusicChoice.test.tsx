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
    expect(screen.getByRole('radio', { name: 'Swedebeat mix' })).toBeDisabled()
  })

  it('reports the checked option and gives every option a 44 px target', () => {
    withRouter(
      <MusicChoice
        songs={{ kind: 'ready', spotifyConnectionId: 's1' }}
        swedebeat={{ kind: 'ready' }}
        value="swedebeat_mix"
        onChange={() => undefined}
      />,
    )
    for (const button of screen.getAllByRole('radio')) {
      expect(button.className).toContain('min-h-11')
    }
    expect(screen.getByRole('radio', { name: 'Swedebeat mix' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'My playlist' })).not.toBeChecked()
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
    fireEvent.click(screen.getByRole('radio', { name: 'Swedebeat mix' }))
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
    expect(screen.getByRole('radiogroup', { name: 'Music' })).toBeInTheDocument()
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
    const button = screen.getByRole('radio', { name: 'No music' })
    expect(button.className).toContain('min-h-11')
    expect(button).not.toBeChecked()
    fireEvent.click(button)
    expect(chosen).toBe('none')
  })

  it('says in plain words what No music means when it is chosen, and only then', () => {
    const { rerender } = withRouter(
      <MusicChoice songs={{ kind: 'off' }} swedebeat={{ kind: 'off' }} soundless value="none" onChange={() => undefined} />,
    )
    expect(screen.getByRole('radio', { name: 'No music' })).toBeChecked()
    expect(screen.getByText(/play your own music/)).toBeInTheDocument()
    rerender(
      <RouterProvider>
        <MusicChoice songs={{ kind: 'off' }} swedebeat={{ kind: 'off' }} soundless value="playlist" onChange={() => undefined} />
      </RouterProvider>,
    )
    expect(screen.queryByText(/play your own music/)).toBeNull()
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
    expect(screen.queryByRole('radio', { name: 'No music' })).toBeNull()
  })
})
