import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { decodeSongList } from '../../api/songs'
import { MusicNotAsked } from './MusicNotAsked'

// Issue #1133: the approval screen says, in plain words, what the chosen songs could not keep to.

const LIST = {
  id: 'r1', number: 2, status: 'waiting', job_id: 'j1', expires_at: null, approved_at: null,
  content_hash: 'h', mode: 'pick', target_sec: 2700, total_sec: 900, omitted_no_bpm: 0, note: '',
  songs: [],
}

describe('MusicNotAsked', () => {
  it('lists the Not as asked items of a song list from the catalogue', () => {
    const list = decodeSongList({
      ...LIST,
      unmet: [
        { path: 'music_era.0', origin: 'not_as_asked', reason_code: 'era_too_few', value: { from: 1980, to: 1989, found: 0 } },
        { path: 'music_length', origin: 'not_as_asked', reason_code: 'songs_too_short', value: { total_min: 15, target_min: 45 } },
        { path: 'music_era.1', origin: 'explicit', value: { from: 1990, to: 1999 } },
      ],
    })
    expect(list.unmet.map((i) => i.path)).toEqual(['music_era.0', 'music_length'])
    render(<MusicNotAsked items={list.unmet} />)
    const box = screen.getByTestId('music-not-asked')
    expect(box.textContent).toContain('These songs do not fully match your request:')
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Music from: 1980 to 1989. We found too few songs from these years on Spotify.',
      'Song list: 15 of 45 minutes. The songs are shorter than the class.',
    ])
  })

  it('shows nothing for a list that kept to the request, or one from before', () => {
    const list = decodeSongList(LIST)
    expect(list.unmet).toEqual([])
    const { container } = render(<MusicNotAsked items={list.unmet} />)
    expect(container.textContent).toBe('')
  })
})
