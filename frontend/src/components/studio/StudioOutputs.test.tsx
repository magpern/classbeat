import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ClassSongs } from '../../api/classSongs'
import type { Output, Timeline } from '../../api/studioOutputs'
import { RouterProvider } from '../../router/Router'
import { StudioOutputs } from './StudioOutputs'

const timeline = {
  revisionId: 'rev-1',
  contentHash: 'hash',
  hasRepeats: false,
  proposalRevisionId: null,
  specDigest: 'digest',
  isPlain: true,
  choices: null,
  crossfades: [],
} as unknown as Timeline

function show(enabled: readonly Output[] | null, onRetry = vi.fn()) {
  render(
    <RouterProvider>
      <StudioOutputs
        classId="c1"
        timeline={timeline}
        songs={{} as unknown as ClassSongs}
        disabled={false}
        enabled={enabled}
        onRetry={onRetry}
      />
    </RouterProvider>,
  )
  return onRetry
}

describe('the outputs a studio offers', () => {
  it('offers none when the enabled list could not be read, with a notice and a retry', () => {
    const onRetry = show(null)
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(screen.getByTestId('studio-outputs-unread')).toHaveTextContent('The outputs could not be loaded. Try again.')
    expect(screen.queryByText(/No output is available/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('says none is on for a design-only studio (an empty list)', () => {
    show([])
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(screen.getByText(/No output is available right now/)).toBeInTheDocument()
    expect(screen.queryByTestId('studio-outputs-unread')).toBeNull()
  })

  it('offers only the outputs on the list', () => {
    show(['spotify_playlist'])
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
    expect(screen.getByRole('checkbox', { name: /Spotify playlist/ })).toBeInTheDocument()
    expect(screen.queryByTestId('studio-outputs-unread')).toBeNull()
  })
})
