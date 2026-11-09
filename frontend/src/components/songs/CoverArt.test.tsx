import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { decodeSongList } from '../../api/songs'
import { CoverArt, CoverMosaic, initials, isCoverUrl, isPlaylistCoverUrl } from './CoverArt'
import { SongRows } from './SongRows'

// Invented URLs only: jsdom never loads an image, and nothing here asks a network.
const HEX = 'ab12cd34ef'.repeat(4)
const HOST = ['https:', '', 'i.scdn.co'].join('/')
const GOOD = `${HOST}/image/${HEX}`

const DROPPED = [
  ['another host', `${['https:', '', 'daily-mix.scdn.co'].join('/')}/image/${HEX}`],
  ['the mosaic host (a playlist cover only)', `${['https:', '', 'mosaic.scdn.co'].join('/')}/300/${HEX.repeat(4)}`],
  ['an uploaded cover (a playlist cover only)', `${['https:', '', 'image-cdn-ak.spotifycdn.com'].join('/')}/image/${HEX}`],
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

// Playlist covers (ADR 0013 section 9, amendment of 2026-11-03, #537).
const url = (host: string, path: string) => `${['https:', '', host].join('/')}${path}`
const PLAYLIST_KEPT = [
  ['the album pin', GOOD],
  ['a 300 px mosaic', url('mosaic.scdn.co', `/300/${HEX.repeat(4)}`)],
  ['a 640 px mosaic', url('mosaic.scdn.co', `/640/${HEX.repeat(4)}`)],
  ['an uploaded cover (ak)', url('image-cdn-ak.spotifycdn.com', `/image/${HEX}`)],
  ['an uploaded cover (fa)', url('image-cdn-fa.spotifycdn.com', `/image/${HEX}`)],
] as const
const PLAYLIST_DROPPED = [
  ...DROPPED.slice(0, 1),
  ...DROPPED.slice(3),
  ['a 60 px mosaic', url('mosaic.scdn.co', `/60/${HEX.repeat(4)}`)],
  ['a mosaic of three', url('mosaic.scdn.co', `/300/${HEX.repeat(3)}`)],
  ['a mosaic with a query', url('mosaic.scdn.co', `/300/${HEX.repeat(4)}?x=1`)],
  ['another image-cdn host', url('image-cdn-xx.spotifycdn.com', `/image/${HEX}`)],
  ['an image-cdn look-alike', url('image-cdn-ak.spotifycdn.com.evil.example', `/image/${HEX}`)],
  ['an uploaded cover with a trailing newline', `${url('image-cdn-ak.spotifycdn.com', `/image/${HEX}`)}\n`],
  ['a Blend cover', url('blend-playlist-covers.spotifycdn.com', `/v2/${HEX}.png`)],
] as const

describe('isPlaylistCoverUrl', () => {
  it.each(PLAYLIST_KEPT)('accepts %s', (_name, value) => {
    expect(isPlaylistCoverUrl(value)).toBe(true)
  })

  it.each(PLAYLIST_DROPPED)('drops %s', (_name, value) => {
    expect(isPlaylistCoverUrl(value)).toBe(false)
  })

  it('shows a playlist cover only with kind="playlist"', () => {
    const mosaic = PLAYLIST_KEPT[1][1]
    const asPlaylist = render(<CoverArt src={mosaic} label="Hills" kind="playlist" size="lg" />)
    expect(asPlaylist.container.querySelector('img')?.getAttribute('src')).toBe(mosaic)
    expect(asPlaylist.container.querySelector('img')?.getAttribute('referrerpolicy')).toBe('no-referrer')
    asPlaylist.unmount()
    const asAlbum = render(<CoverArt src={mosaic} label="Hills" />)
    expect(asAlbum.container.querySelector('img')).toBeNull()
  })

  it('lets a long unbroken title and artist wrap, so a 320 px row never scrolls sideways', () => {
    const long = 'A'.repeat(120)
    const song = { position: 1, title: long, artist: long, role: 'warmup', bpm: 100, length_sec: 240, new: false }
    const list = decodeSongList({
      id: 'r', number: 1, status: 'waiting', job_id: null, expires_at: null, approved_at: null,
      content_hash: 'h', mode: 'pick', target_sec: 480, total_sec: 471, omitted_no_bpm: 0, note: '',
      songs: [song],
    })
    render(<SongRows songs={list.songs} />)
    expect(screen.getByTestId('song-title')).toHaveClass('break-words')
    expect(screen.getByText(long, { selector: 'span.block' })).toHaveClass('break-words')
  })
})
