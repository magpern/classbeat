import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DraftSegment } from '../../api/draftTypes'
import type { Song } from '../../api/songs'
import { SegmentList } from './SegmentList'

// ADR 0040 section 3 (#1303 stage C): the composer's song rows label a song whose timing is
// approximate, as the song list does; every other song has no mark.

const segment = (name: string, sec: number): DraftSegment => ({
  name, kind: 'main', intervals: [{ sec, zone: 2, rpm: [85, 95], standing: false, cue: '' }],
})
const song = (position: number, startSec: number, timing: Song['timing']): Song => ({
  position, title: `Song ${String(position)}`, artist: 'Band', role: 'main', bpm: 120, lengthSec: 240, startSec,
  isNew: false, cover: null, openSpotify: null, timing,
})

describe('the composer segment list', () => {
  it('marks only the approximate song', () => {
    render(
      <SegmentList
        segments={[segment('One', 240), segment('Two', 240)]}
        songs={[song(1, 0, null), song(2, 240, 'approximate')]}
        hasList
        attached={0}
        canSwap={false}
        swapping={false}
        onSwap={() => undefined}
      />,
    )
    const marks = screen.getAllByTestId('song-timing-approximate')
    expect(marks).toHaveLength(1)
    expect(marks[0]?.textContent).toBe('Timing approximate')
    expect(marks[0]?.parentElement?.textContent).toContain('Song 2')
  })
})
