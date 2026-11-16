import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Handoff } from '../../hooks/useHandoff'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { HandoffStatus } from './HandoffStatus'

function show(refusal: string) {
  const handoff: Handoff = {
    phase: 'refused',
    items: [],
    interpretJobId: null,
    refusal,
    held: null,
    designWithinPlan: vi.fn(),
    retry: vi.fn(),
  }
  return render(
    <I18nProvider initial="en">
      <RouterProvider initialLocation={{ pathname: '/en/classes/c1', search: '' }}>
        <HandoffStatus classId="c1" handoff={handoff} />
      </RouterProvider>
    </I18nProvider>,
  )
}

describe('HandoffStatus', () => {
  it('says a class with music needs Spotify, with a link to connect it', async () => {
    show('needs_spotify')
    expect(await screen.findByText('A class with music needs your Spotify account.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/en/account/spotify')
  })

  it('does not offer the Spotify link for another refusal', async () => {
    show('plan_limit')
    await screen.findByTestId('handoff-stopped')
    expect(screen.queryByRole('link', { name: 'Connect Spotify' })).toBeNull()
  })
})
