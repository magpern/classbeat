import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { MusicSourcePicker } from './MusicSourcePicker'
import type { MusicRow } from './musicSources'

// The list sits in a `flex` box that carries the `hidden` attribute while the picker is closed. jsdom applies no
// CSS, so what this checks is the attribute; that it hides the box is the rule in styles/tailwind.css, guarded by
// styles/hiddenAttribute.test.ts and checked in a browser (#1194).
const ROWS: MusicRow[] = [
  { key: 'playlist:a', title: 'Hill climbs', source: 'Spotify playlist · in MOWL', override: { source: 'mowl_spotify_playlist', spotify_playlist_id: 'a' } },
]

function show() {
  return render(
    <I18nProvider initial="en">
      <MusicSourcePicker rows={ROWS} value={null} onChange={() => undefined} />
    </I18nProvider>,
  )
}

describe('MusicSourcePicker', () => {
  it('keeps its list hidden until the Music button is clicked, and hides it again on a second click', () => {
    show()
    const button = screen.getByRole('button', { name: 'Music' })
    const list = document.getElementById(button.getAttribute('aria-controls') ?? '')
    expect(list).not.toBeNull()
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(list).toHaveAttribute('hidden')
    expect(screen.queryByRole('radio', { name: /Hill climbs/ })).toBeNull()
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(list).not.toHaveAttribute('hidden')
    expect(screen.getByRole('radio', { name: /Hill climbs/ })).toBeInTheDocument()
    fireEvent.click(button)
    expect(list).toHaveAttribute('hidden')
  })
})
