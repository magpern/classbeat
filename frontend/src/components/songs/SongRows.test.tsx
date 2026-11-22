import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { decodeSongList } from '../../api/songs'
import da from '../../i18n/da'
import { en } from '../../i18n/en'
import sv from '../../i18n/sv'
import { SongRows } from './SongRows'

// ADR 0040 section 3 (#1303 stage C): a song whose timing is approximate (a YouTube match) is
// labelled "timing approximate" where the class's songs are listed; every other song is not.

const row = (position: number, more: Record<string, unknown> = {}) => ({
  position, title: `Song ${String(position)}`, artist: 'Band', role: 'warmup', bpm: 120, length_sec: 240, new: false, ...more,
})
const list = (songs: Record<string, unknown>[]) =>
  decodeSongList({
    id: 'l', number: 1, status: 'waiting', content_hash: 'h', mode: 'pick', target_sec: 720, total_sec: 701,
    crossfade_sec: 9.54, songs,
  })

describe('the timing approximate label', () => {
  it('is read only as approximate; anything else is no mark', () => {
    const decoded = list([
      row(1, { timing: 'approximate' }),
      row(2),
      row(3, { timing: 'exact' }),
      row(4, { timing: 'APPROXIMATE' }),
      row(5, { timing: null }),
    ])
    expect(decoded.songs.map((s) => s.timing)).toEqual(['approximate', null, null, null, null])
  })

  it('marks only the approximate song in the list', () => {
    render(<SongRows songs={list([row(1), row(2, { timing: 'approximate' }), row(3, { new: true })]).songs} />)
    const marks = screen.getAllByTestId('song-timing-approximate')
    expect(marks).toHaveLength(1)
    expect(marks[0]?.textContent).toBe('Timing approximate')
    expect(marks[0]?.closest('[data-testid="song"]')?.textContent).toContain('Song 2')
    expect(screen.getByText('New')).toBeTruthy() // the New mark is kept beside it
  })

  it('shows no mark for a list without timing', () => {
    render(<SongRows songs={list([row(1), row(2)]).songs} />)
    expect(screen.queryByTestId('song-timing-approximate')).toBeNull()
  })

  it('is worded in en, sv and da', () => {
    expect(en['songs.list.timingApproximate']).toBe('Timing approximate')
    expect(sv['songs.list.timingApproximate']).toBe('Ungefärlig timing')
    expect(da['songs.list.timingApproximate']).toBe('Omtrentlig timing')
  })
})

// #1340 (report only): a song with explicit lyrics shows a small Explicit tag; unknown shows nothing.
describe('the explicit tag', () => {
  it('is read only as true; anything else is no tag', () => {
    const decoded = list([
      row(1, { explicit: true }),
      row(2),
      row(3, { explicit: false }),
      row(4, { explicit: 'true' }),
      row(5, { explicit: null }),
    ])
    expect(decoded.songs.map((s) => s.explicit)).toEqual([true, null, null, null, null])
  })

  it('tags only the explicit song, with an accessible label and a tooltip, beside the timing mark', () => {
    render(<SongRows songs={list([row(1), row(2, { explicit: true, timing: 'approximate' })]).songs} />)
    const tags = screen.getAllByTestId('song-explicit')
    expect(tags).toHaveLength(1)
    expect(tags[0]?.getAttribute('aria-label')).toBe('Explicit lyrics')
    expect(tags[0]?.getAttribute('title')).toBe('Explicit lyrics')
    expect(tags[0]?.closest('[data-testid="song"]')?.textContent).toContain('Song 2')
    expect(screen.getByTestId('song-timing-approximate')).toBeTruthy()
  })

  it('shows no tag for a list without the flag', () => {
    render(<SongRows songs={list([row(1), row(2)]).songs} />)
    expect(screen.queryByTestId('song-explicit')).toBeNull()
  })

  it('is worded in en, sv and da', () => {
    expect(en['songs.list.explicit']).toBe('Explicit lyrics')
    expect(sv['songs.list.explicit']).toBe('Explicit text')
    expect(da['songs.list.explicit']).toBe('Eksplicit tekst')
  })
})
