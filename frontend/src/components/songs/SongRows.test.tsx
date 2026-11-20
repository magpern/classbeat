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
    id: 'l', number: 1, status: 'waiting', content_hash: 'h', mode: 'pick', target_sec: 720, total_sec: 702,
    crossfade_sec: 9, songs,
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
