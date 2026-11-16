import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { SpotifyPrompt } from './SpotifyPrompt'

function show(need: 'connect' | 'consent', language: 'en' | 'sv' | 'da' = 'en') {
  return render(
    <I18nProvider initial={language}>
      <RouterProvider initialLocation={{ pathname: `/${language}/classes/new`, search: '' }}>
        <SpotifyPrompt need={need} />
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
})
