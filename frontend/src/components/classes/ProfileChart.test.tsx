import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DraftSegment } from '../../api/jobs'
import type { Song } from '../../api/songs'
import { heightShare } from '../../lib/profile'
import { ProfileChart } from './ProfileChart'
import { ZoneTime } from './ZoneTime'
import { flatten } from '../../lib/profile'

const SEGMENTS: DraftSegment[] = [
  {
    name: 'Warm up',
    kind: 'warmup',
    intervals: [{ sec: 300, zone: 2, rpm: [80, 90], standing: false, cue: 'Easy now' }],
  },
  {
    name: 'Work',
    kind: 'main',
    intervals: [
      { sec: 600, zone: 6, rpm: [60, 70], standing: true, cue: 'Push' },
      { sec: 300, zone: 7, rpm: 'beat', standing: false, cue: '' },
    ],
  },
]

const SONGS: Song[] = [
  { position: 1, title: 'First', artist: 'A', role: 'warmup', bpm: 120, lengthSec: 600, isNew: false },
  { position: 2, title: 'Second', artist: 'B', role: 'main', bpm: 140, lengthSec: 600, isNew: false },
]

function bars(container: HTMLElement) {
  return Array.from(container.querySelectorAll('[data-bar]'))
}

describe('the class profile', () => {
  it('draws bar widths from the time and heights from the zone', () => {
    const { container } = render(<ProfileChart segments={SEGMENTS} title="Hills" songs={null} />)
    const all = bars(container)
    expect(all).toHaveLength(3)
    const widths = all.map((b) => Number(b.getAttribute('width')))
    // 300 s, 600 s and 300 s of 1200 s: the middle bar is twice as wide as the others (each loses a 1 unit gap).
    expect((widths[1] ?? 0) + 1).toBeCloseTo(2 * ((widths[0] ?? 0) + 1), 5)
    expect(widths[2]).toBeCloseTo(widths[0] ?? 0, 5)
    const heights = all.map((b) => Number(b.getAttribute('height')))
    expect((heights[1] ?? 0) / (heights[0] ?? 1)).toBeCloseTo(heightShare(6) / heightShare(2), 5)
    expect(heights[2]).toBeGreaterThan(heights[1] ?? 0)
    expect(all.map((b) => b.getAttribute('data-zone'))).toEqual(['2', '6', '7'])
  })

  it('puts a flame above zone 6 and a bolt above zone 7 only', () => {
    render(<ProfileChart segments={SEGMENTS} title="Hills" songs={null} />)
    expect(screen.getAllByTestId('marker-flame')).toHaveLength(1)
    expect(screen.getAllByTestId('marker-bolt')).toHaveLength(1)
  })

  it('has no markers without zone 6 or 7', () => {
    render(
      <ProfileChart
        segments={[{ name: 'Easy', kind: 'main', intervals: [{ sec: 600, zone: 3, rpm: 'beat', standing: false, cue: '' }] }]}
        title="Easy"
        songs={null}
      />,
    )
    expect(screen.queryByTestId('marker-flame')).toBeNull()
    expect(screen.queryByTestId('marker-bolt')).toBeNull()
  })

  it('is an image with a summary and has a time axis every 5 minutes', () => {
    render(<ProfileChart segments={SEGMENTS} title="Hills" songs={null} />)
    expect(screen.getByRole('img', { name: /Effort over time for Hills: 2 segments, 20:00 in total. The hardest zone is 7/ })).toBeInTheDocument()
    expect(screen.getByText('5 min')).toBeInTheDocument()
    expect(screen.getByText('15 min')).toBeInTheDocument()
    expect(screen.queryByText('20 min')).toBeNull()
  })

  it('selects an interval with the arrow keys and shows it in the detail bar', () => {
    render(<ProfileChart segments={SEGMENTS} title="Hills" songs={null} />)
    const detail = screen.getByTestId('profile-detail')
    expect(detail).toHaveTextContent('Select a bar')
    const chart = screen.getByTestId('profile')
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(detail).toHaveTextContent('Zone 2 Endurance')
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(detail).toHaveTextContent('Zone 6 Anaerobic, 5:00 to 15:00, 60 to 70 rpm, standing: Push')
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(detail).toHaveTextContent('Zone 7 Neuromuscular, 15:00 to 20:00, on the beat, seated')
    fireEvent.keyDown(chart, { key: 'ArrowRight' })
    expect(detail).toHaveTextContent('Zone 7 Neuromuscular')
    fireEvent.keyDown(chart, { key: 'Home' })
    expect(detail).toHaveTextContent('Zone 2 Endurance')
    fireEvent.keyDown(chart, { key: 'Escape' })
    expect(detail).toHaveTextContent('Select a bar')
  })

  it('selects a bar on hover and on tap', () => {
    const { container } = render(<ProfileChart segments={SEGMENTS} title="Hills" songs={null} />)
    fireEvent.mouseEnter(bars(container)[1]!)
    expect(screen.getByTestId('profile-detail')).toHaveTextContent('Zone 6 Anaerobic')
    fireEvent.click(bars(container)[2]!)
    expect(screen.getByTestId('profile-detail')).toHaveTextContent('Zone 7 Neuromuscular')
  })

  it('hides the song strip without songs and shows the songs when there are some', () => {
    const { rerender } = render(<ProfileChart segments={SEGMENTS} title="Hills" songs={null} />)
    expect(screen.queryByTestId('song-strip')).toBeNull()
    rerender(<ProfileChart segments={SEGMENTS} title="Hills" songs={[]} />)
    expect(screen.queryByTestId('song-strip')).toBeNull()
    rerender(<ProfileChart segments={SEGMENTS} title="Hills" songs={SONGS} />)
    expect(screen.getByTestId('song-strip')).toBeInTheDocument()
    expect(screen.getByText('First')).toBeInTheDocument()
  })

  it('lists the time in each of the seven zones', () => {
    render(<ZoneTime bars={flatten(SEGMENTS)} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(7)
    expect(screen.getByText('Z6 Anaerobic').closest('li')).toHaveTextContent('10:00')
    expect(screen.getByText('Z1 Recovery').closest('li')).toHaveTextContent('0:00')
  })
})
