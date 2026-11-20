import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { PlaylistTiles } from './PlaylistTiles'

// The Spotify icon badge on a playlist cover (ADR 0013 section 9, #1288). Invented URLs only:
// jsdom never loads an image, and nothing here asks a network.
const HEX = 'ab12cd34ef'.repeat(4)
const COVER = `${['https:', '', 'i.scdn.co'].join('/')}/image/${HEX}`
const HILLS = 'AbCdEfGhIjKlMnOpQrStUv'
const SPRINTS = 'ZyXwVuTsRqPoNmLkJiHgFe'
const PLAYLISTS = [
  { spotifyPlaylistId: HILLS, name: 'Hills' },
  { spotifyPlaylistId: SPRINTS, name: 'Sprint Mix' },
]

function renderTiles(covers: ReadonlyMap<string, string>) {
  return render(
    <I18nProvider>
      <PlaylistTiles playlists={PLAYLISTS} value="" onChange={() => undefined} covers={covers} />
    </I18nProvider>,
  )
}

function tile(name: string): HTMLElement {
  const label = screen.getByRole('radio', { name }).closest('label')
  if (label === null) throw new Error(`no tile for ${name}`)
  return label
}

const badge = (el: HTMLElement) => el.querySelector('[data-testid="spotify-cover-badge"]')

describe('the Spotify badge on a playlist cover', () => {
  it('shows on a tile that shows a cover: the bundled icon, both theme files, decorative', () => {
    renderTiles(new Map([[HILLS, COVER]]))
    const hills = badge(tile('Hills'))
    expect(hills).not.toBeNull()
    const files = [...(hills?.querySelectorAll('img') ?? [])]
    expect(files.map((img) => img.className)).toEqual(['brand-icon brand-icon__dark', 'brand-icon brand-icon__light'])
    for (const img of files) {
      expect(img.getAttribute('alt')).toBe('')
      expect(img.getAttribute('src')).toBeTruthy()
    }
    // The badge sits inside the aria-hidden cover, so no screen reader meets it.
    expect(hills?.closest('[aria-hidden="true"]')).toBe(within(tile('Hills')).getByTestId('playlist-cover'))
  })

  it('is absent on a tile without a cover (initials)', () => {
    renderTiles(new Map([[HILLS, COVER]]))
    expect(badge(tile('Sprint Mix'))).toBeNull()
    expect(within(tile('Sprint Mix')).getByText('SM')).toBeInTheDocument()
  })

  it('is absent when the cover URL fails the pin (initials, no remote image)', () => {
    renderTiles(new Map([[HILLS, `${COVER}?x=1`]]))
    expect(badge(tile('Hills'))).toBeNull()
    expect(within(tile('Hills')).getByTestId('cover-tile')).toBeInTheDocument()
  })

  it('goes away when the cover image fails to load, and only on that tile', () => {
    renderTiles(
      new Map([
        [HILLS, COVER],
        [SPRINTS, COVER],
      ]),
    )
    expect(document.querySelectorAll('[data-testid="spotify-cover-badge"]')).toHaveLength(2)
    const cover = tile('Hills').querySelector('img[referrerpolicy]')
    expect(cover).not.toBeNull()
    fireEvent.error(cover as Element)
    expect(badge(tile('Hills'))).toBeNull()
    expect(within(tile('Hills')).getByText('H')).toBeInTheDocument()
    expect(badge(tile('Sprint Mix'))).not.toBeNull()
  })

  it('leaves the radio group as it was: the name labels each radio, the badge adds no name or tab stop', () => {
    renderTiles(new Map([[HILLS, COVER]]))
    const group = screen.getByRole('radiogroup', { name: 'Playlist' })
    expect(within(group).getAllByRole('radio').map((r) => r.getAttribute('value'))).toEqual([HILLS, SPRINTS])
    expect(screen.getByRole('radio', { name: 'Hills' })).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
    expect(badge(tile('Hills'))?.querySelector('button, a, input, [tabindex]')).toBeNull()
  })
})
