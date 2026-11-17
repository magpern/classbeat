import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { SpotifyPrompt } from './SpotifyPrompt'

function show(need: 'connect' | 'consent' | 'unavailable', language: 'en' | 'sv' | 'da' = 'en', soundless = false) {
  return render(
    <I18nProvider initial={language}>
      <RouterProvider initialLocation={{ pathname: `/${language}/classes/new`, search: '' }}>
        <SpotifyPrompt need={need} soundless={soundless} />
      </RouterProvider>
    </I18nProvider>,
  )
}

describe('SpotifyPrompt', () => {
  it.each([
    ['en', 'Connect Spotify', /needs Spotify/],
    ['sv', 'Anslut Spotify', /kräver Spotify/],
    ['da', 'Tilslut Spotify', /kræver Spotify/],
  ] as const)('tells a %s instructor with no Spotify to connect it, with a link to the Spotify page', async (language, link, words) => {
    show('connect', language)
    expect(await screen.findByRole('link', { name: link })).toHaveAttribute('href', `/${language}/account/spotify`)
    expect(screen.getByTestId('spotify-prompt')).toHaveTextContent(words)
  })

  it('asks for the working playlist when Spotify is connected but not allowed yet', () => {
    show('consent')
    expect(screen.getByTestId('spotify-prompt')).toHaveAttribute('data-need', 'consent')
    expect(screen.getByRole('link', { name: 'Open the Spotify settings' })).toHaveAttribute('href', '/en/account/spotify')
  })

  it.each([
    ['en', /Choosing songs is not available on your account/],
    ['sv', /Att välja låtar är inte tillgängligt för ditt konto/],
    ['da', /Valg af sange er ikke tilgængeligt for din konto/],
  ] as const)('tells a %s instructor that song picking is not available, with no link', (language, words) => {
    show('unavailable', language)
    const prompt = screen.getByTestId('spotify-prompt')
    expect(prompt).toHaveAttribute('data-need', 'unavailable')
    expect(prompt).toHaveTextContent(words)
    expect(screen.queryByRole('link')).toBeNull()
    expect(prompt).not.toHaveTextContent(/connect|anslut|tilslut/i)
  })

  it.each([
    ['en', 'connect', 'To ride without music, choose "No music" in the music list.'],
    ['en', 'unavailable', 'To ride without music, choose "No music" in the music list.'],
    ['sv', 'unavailable', 'Vill du köra utan musik, välj "Ingen musik" i musiklistan.'],
    ['da', 'unavailable', 'Vil du køre uden musik, så vælg "Ingen musik" på musiklisten.'],
  ] as const)('points a %s instructor to "No music" when it is offered (%s)', (language, need, hint) => {
    show(need, language, true)
    expect(screen.getByTestId('spotify-prompt')).toHaveTextContent(hint)
  })

  it('does not mention "No music" when it is not offered, or when the working playlist is the problem', () => {
    show('unavailable')
    expect(screen.getByTestId('spotify-prompt')).not.toHaveTextContent('No music')
    show('consent', 'en', true)
    expect(screen.getAllByTestId('spotify-prompt')[1]).not.toHaveTextContent('No music')
  })
})
