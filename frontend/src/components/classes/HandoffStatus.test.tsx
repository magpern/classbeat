import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { fakeApi, reason } from '../../test/fakeApi'
import { SESSION_ROUTES } from '../../test/jobFixtures'
import { NO_EXTRAS } from '../../api/readingExtras'
import type { Handoff } from '../../hooks/useHandoff'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { HandoffStatus } from './HandoffStatus'

function show(refusal: string, language: 'en' | 'sv' | 'da' = 'en', retry = vi.fn()) {
  const handoff: Handoff = {
    phase: 'refused',
    items: [],
    extras: NO_EXTRAS,
    interpretJobId: null,
    refusal,
    held: null,
    designWithinPlan: vi.fn(),
    retry,
  }
  return render(
    <I18nProvider initial={language}>
      <RouterProvider initialLocation={{ pathname: `/${language}/classes/c1`, search: '' }}>
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

  it.each([
    ['en', 'Choosing songs is not available on your account, so a class with music cannot be made.'],
    ['sv', 'Att välja låtar är inte tillgängligt för ditt konto, så ett pass med musik kan inte skapas.'],
    ['da', 'Valg af sange er ikke tilgængeligt for din konto, så et hold med musik kan ikke laves.'],
  ] as const)('says in %s that song picking is not available, with no link to connect Spotify', async (language, words) => {
    show('song_picking_unavailable', language)
    expect(await screen.findByText(words)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Spotify/ })).toBeNull()
    expect(screen.queryByText(/needs your Spotify/)).toBeNull()
  })

  it('does not offer the Spotify link for another refusal', async () => {
    show('plan_limit')
    await screen.findByTestId('handoff-stopped')
    expect(screen.queryByRole('link', { name: 'Connect Spotify' })).toBeNull()
  })
})

// Every reason the backend stores on an interpretation (ADR 0038 amendment of 2026-11-24, decision 3):
// its own line, the actions that can help, and none that blames the request for a limit that is not.
const LINKS = { account: 'Go to your account', spotify: 'Connect Spotify', again: 'Try again with the same request', classes: 'My classes' }
const NOW = 'Try again'
const REASONS: [string, string, string, string[]][] = [
  ['invalid_input', 'request', 'Your request could not be used as written. Change it, then try again.', [LINKS.again]],
  ['too_large', 'request', 'Your request is too long to design in one go. Shorten it, then try again.', [LINKS.again]],
  ['needs_spotify', 'account', 'A class with music needs your Spotify account.', [LINKS.spotify, LINKS.again]],
  ['song_picking_unavailable', 'account', 'Choosing songs is not available on your account, so a class with music cannot be made.', [LINKS.again]],
  ['consent_missing', 'account', 'Allow the working playlist in your Spotify settings on your account page first.', [LINKS.spotify, NOW]],
  ['quota', 'account', 'You have used up what your plan allows for now. Nothing was charged and your request is kept.', [LINKS.account]],
  ['revision_limit', 'account', 'This class has reached the number of changes your plan allows. Start a new class to keep going.', [LINKS.account]],
  ['feature_off', 'site', 'Designing classes this way is switched off here right now. Nothing was charged and your request is kept.', []],
  ['soundless_not_evaluated', 'site', 'Designing classes without music is not available here right now. Nothing was charged and your request is kept.', [LINKS.account]],
  ['needs_mowl_playlist', 'account', 'Running needs a playlist from your MOWL music.', [LINKS.account, LINKS.again]],
  ['busy', 'transient', 'Your other designs are still running. Nothing was charged and your request is kept. Try again in a moment.', [NOW]],
  ['pool_busy', 'transient', 'Another song pick is still running. Nothing was charged and your request is kept. Try again in a moment.', [NOW]],
  ['class_busy', 'transient', 'A design of this class is still running. Wait for it to finish, then try again.', [NOW]],
  ['not_ready', 'transient', 'Your request is still being read. Wait a moment, then try again.', [NOW]],
  ['class_designed', 'state', 'This class has already been designed. Open your classes to see it.', [LINKS.classes]],
  ['something_new', 'other', 'classbeat could not start this design. Nothing was charged and your request is kept.', [LINKS.again]],
]

describe('HandoffStatus: one line per stored reason', () => {
  it.each(REASONS)('%s (%s) says what happened and offers only what can help', async (reason, kind, words, offered) => {
    show(reason)
    const box = await screen.findByTestId('handoff-stopped')
    expect(box).toHaveAttribute('data-kind', kind)
    expect(screen.getByText(words)).toBeInTheDocument()
    const shown = [...screen.queryAllByRole('link'), ...screen.queryAllByRole('button')].map((e) => e.textContent)
    expect(shown.sort()).toEqual([...offered].sort())
    // No reason falls through to words that blame the request.
    expect(screen.queryByText(/could not be turned into a class as asked/)).toBeNull()
  })

  it.each(['feature_off', 'soundless_not_evaluated'])('%s offers no retry of any kind: a retry cannot succeed here', async (reason) => {
    show(reason)
    await screen.findByTestId('handoff-stopped')
    expect(screen.queryByText(NOW)).toBeNull()
    expect(screen.queryByRole('link', { name: LINKS.again })).toBeNull()
  })

  it.each(REASONS.map((r) => r[0]))('%s has a line in sv and da that is not the English one', async (reason) => {
    for (const language of ['sv', 'da'] as const) {
      const { container, unmount } = show(reason, language)
      await screen.findByTestId('handoff-stopped')
      const text = container.querySelector('[data-testid="handoff-stopped"]')?.textContent ?? ''
      expect(text).not.toMatch(/ the | your | request /)
      expect(text).not.toBe('')
      unmount()
    }
  })

  it('asks the server once more with retry, then reads the hand-off again, for a transient reason', async () => {
    const user = userEvent.setup()
    const reread = vi.fn()
    const api = fakeApi({
      ...SESSION_ROUTES,
      'POST /api/classes/c1/design-from-interpretation': { job_id: 'j1' },
    })
    show('busy', 'en', reread)
    await user.click(await screen.findByRole('button', { name: NOW }))
    await waitFor(() => {
      expect(reread).toHaveBeenCalledTimes(1)
    })
    expect(api.to('POST', '/api/classes/c1/design-from-interpretation')[0]?.body).toEqual({ retry: true })
  })

  it('reads the hand-off again after a 429 busy answer to the retry, not only after a 409', async () => {
    const user = userEvent.setup()
    const reread = vi.fn()
    fakeApi({
      ...SESSION_ROUTES,
      'POST /api/classes/c1/design-from-interpretation': reason(429, 'busy'),
    })
    show('busy', 'en', reread)
    await user.click(await screen.findByRole('button', { name: NOW }))
    await waitFor(() => {
      expect(reread).toHaveBeenCalledTimes(1)
    })
  })
})
