import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { decodeSongList } from '../../api/songs'
import { CoverArt, CoverMosaic, initials, isCoverUrl } from './CoverArt'
import { SongRows } from './SongRows'

// Invented URLs only: jsdom never loads an image, and nothing here asks a network.
const HEX = 'ab12cd34ef'.repeat(4)
const HOST = ['https:', '', 'i.scdn.co'].join('/')
const GOOD = `${HOST}/image/${HEX}`

const DROPPED = [
  ['another host', `${['https:', '', 'mosaic.scdn.co'].join('/')}/image/${HEX}`],
  ['evil host', `${['https:', '', 'evil.example'].join('/')}/image/${HEX}`],
  ['host suffix', `${HOST}.evil.example/image/${HEX}`],
  ['http', `${GOOD.replace('https:', 'http:')}`],
  ['uppercase hex', `${HOST}/image/${HEX.toUpperCase()}`],
  ['39 hex', `${HOST}/image/${HEX.slice(1)}`],
  ['41 hex', `${GOOD}a`],
  ['trailing newline', `${GOOD}\n`],
  ['query', `${GOOD}?size=640`],
  ['javascript', `javascript:alert(1)//${GOOD}`],
  ['empty', ''],
] as const

describe('isCoverUrl', () => {
  it('accepts the pinned host with 40 lowercase hex digits', () => {
    expect(isCoverUrl(GOOD)).toBe(true)
  })

  it.each(DROPPED)('drops %s', (_name, url) => {
    expect(isCoverUrl(url)).toBe(false)
  })

  it('drops what is not a string', () => {
    for (const value of [null, undefined, 42, [GOOD], { url: GOOD }]) expect(isCoverUrl(value)).toBe(false)
  })
})

describe('CoverArt', () => {
  it('loads a valid cover lazily, with no referrer and no alt text of its own', () => {
    const { container } = render(<CoverArt src={GOOD} label="Ride Song" />)
    const img = container.querySelector('img')
    expect(img).not.toBeNull()
    expect(img?.getAttribute('src')).toBe(GOOD)
    expect(img?.getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(img?.getAttribute('loading')).toBe('lazy')
    expect(img?.getAttribute('alt')).toBe('')
    expect(img?.hasAttribute('crossorigin')).toBe(false)
  })

  it.each(DROPPED)('shows the initials tile, never an image, for %s', (_name, url) => {
    const { container } = render(<CoverArt src={url} label="Ride Song" />)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('cover-tile').textContent).toBe('RS')
  })

  it('shows the tile without a cover and after a failed load', () => {
    const { container, rerender } = render(<CoverArt src={null} label="ride" />)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('cover-tile').textContent).toBe('R')
    rerender(<CoverArt src={GOOD} label="ride" />)
    const img = container.querySelector('img')
    expect(img).not.toBeNull()
    fireEvent.error(img!)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('cover-tile')).toBeTruthy()
  })

  it('takes initials from letters and digits only', () => {
    expect(initials('  (live) über 2 night  ')).toBe('LÜ')
    expect(initials('— 99 Luftballons')).toBe('9L')
    expect(initials('')).toBe('')
  })
})

describe('CoverMosaic', () => {
  it('shows the first four covers, neutral cells for fewer songs', () => {
    const { container, rerender } = render(
      <CoverMosaic
        songs={[1, 2, 3, 4, 5].map((n) => ({ cover: `${HOST}/image/${String(n).repeat(40)}`, title: `S${n}` }))}
      />,
    )
    const srcs = [...container.querySelectorAll('img')].map((img) => img.getAttribute('src'))
    expect(srcs).toEqual([1, 2, 3, 4].map((n) => `${HOST}/image/${String(n).repeat(40)}`))
    rerender(<CoverMosaic songs={[{ cover: null, title: 'Only One' }]} />)
    expect(container.querySelectorAll('img')).toHaveLength(0)
    expect(screen.getByTestId('cover-mosaic').children).toHaveLength(4)
  })
})

describe('the song rows', () => {
  it('show each song cover through CoverArt and a tile for a v: 1 song without one', () => {
    const song = { position: 1, title: 'Ride Song', artist: 'Band', role: 'warmup', bpm: 100, length_sec: 240, new: true }
    const list = decodeSongList({
      id: 'r', number: 1, status: 'waiting', job_id: null, expires_at: null, approved_at: null,
      content_hash: 'h', mode: 'pick', target_sec: 480, total_sec: 471, omitted_no_bpm: 0, note: '',
      songs: [{ ...song, cover: GOOD }, { ...song, position: 2, cover: `${GOOD}?x` }, { ...song, position: 3 }],
    })
    expect(list.songs.map((s) => s.cover)).toEqual([GOOD, `${GOOD}?x`, null])
    const { container } = render(<SongRows songs={list.songs} />)
    expect([...container.querySelectorAll('img')].map((img) => img.getAttribute('src'))).toEqual([GOOD])
    expect(screen.getAllByTestId('cover-tile')).toHaveLength(2)
  })
})
