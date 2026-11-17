import { describe, expect, it } from 'vitest'
import {
  MAX_MUSIC_TAGS,
  NO_TAGS,
  addPlaylist,
  attachedPlaylists,
  canAttach,
  forSport,
  gateOverride,
  musicInput,
  removePlaylist,
  searchPlaylists,
  tagsFromKept,
  toggleNone,
  togglePlaylist,
  type MusicTags,
  type PlaylistTag,
} from './musicTags'

const tag = (n: number): PlaylistTag => ({ id: `playlist${String(n)}`, name: `List ${String(n)}` })
const ids = (state: MusicTags) => attachedPlaylists(state).map((t) => t.id)
const full = (): MusicTags => [1, 2, 3, 4, 5].reduce<MusicTags>((state, n) => addPlaylist(state, tag(n)), NO_TAGS)

describe('the music tags', () => {
  it('adds playlists in order and ignores a repeat', () => {
    const state = addPlaylist(addPlaylist(addPlaylist(NO_TAGS, tag(1)), tag(2)), tag(1))
    expect(ids(state)).toEqual(['playlist1', 'playlist2'])
  })

  it('stops at the cap of 5 and leaves the state as it was', () => {
    const state = full()
    expect(MAX_MUSIC_TAGS).toBe(5)
    expect(ids(state)).toHaveLength(5)
    expect(canAttach(state)).toBe(false)
    expect(addPlaylist(state, tag(6))).toBe(state)
    expect(togglePlaylist(state, tag(6))).toBe(state)
  })

  it('removes a tag, which makes room again', () => {
    const state = removePlaylist(full(), 'playlist3')
    expect(ids(state)).toEqual(['playlist1', 'playlist2', 'playlist4', 'playlist5'])
    expect(canAttach(state)).toBe(true)
    expect(ids(togglePlaylist(state, tag(3)))).toContain('playlist3')
  })

  it('toggles a playlist off when it is attached', () => {
    expect(ids(togglePlaylist(addPlaylist(NO_TAGS, tag(1)), tag(1)))).toEqual([])
  })

  it('lets "No music" clear the playlists, and a playlist clear "No music"', () => {
    const none = toggleNone(full())
    expect(none).toEqual({ kind: 'none' })
    expect(ids(none)).toEqual([])
    expect(ids(addPlaylist(none, tag(2)))).toEqual(['playlist2'])
    expect(ids(togglePlaylist(none, tag(2)))).toEqual(['playlist2'])
  })

  it('takes "No music" away by choosing it again, and by removing its tag', () => {
    expect(toggleNone({ kind: 'none' })).toEqual(NO_TAGS)
    expect(removePlaylist({ kind: 'none' }, 'playlist1')).toEqual({ kind: 'none' })
  })

  it('sends music_sources for playlists, music_override for "No music", and nothing without tags', () => {
    expect(musicInput(addPlaylist(addPlaylist(NO_TAGS, tag(1)), tag(2)))).toEqual({
      music_sources: [
        { kind: 'spotify_playlist', id: 'playlist1' },
        { kind: 'spotify_playlist', id: 'playlist2' },
      ],
    })
    expect(musicInput({ kind: 'none' })).toEqual({ music_override: { source: 'none' } })
    expect(musicInput(NO_TAGS)).toEqual({})
  })

  it('never sends both inputs, whatever the order of the clicks', () => {
    for (const state of [full(), toggleNone(full()), addPlaylist(toggleNone(full()), tag(1)), toggleNone(toggleNone(full()))]) {
      const sent = musicInput(state)
      expect('music_sources' in sent && 'music_override' in sent).toBe(false)
    }
  })

  it('tells the Spotify gate what it reads: no choice, a playlist, or "No music"', () => {
    expect(gateOverride(NO_TAGS)).toBeNull()
    expect(gateOverride(addPlaylist(NO_TAGS, tag(1)))).toEqual({ source: 'mowl_spotify_playlist', spotify_playlist_id: 'playlist1' })
    expect(gateOverride({ kind: 'none' })).toEqual({ source: 'none' })
  })

  it('restores kept ids that are still on offer, in kept order, up to the cap', () => {
    const offered = [1, 2, 3, 4, 5, 6, 7].map(tag)
    expect(ids(tagsFromKept(['playlist3', 'gone', 'playlist1'], offered))).toEqual(['playlist3', 'playlist1'])
    expect(ids(tagsFromKept(offered.map((t) => t.id), offered))).toHaveLength(5)
  })

  it('drops "No music" for a running class only', () => {
    expect(forSport({ kind: 'none' }, true)).toEqual(NO_TAGS)
    expect(forSport({ kind: 'none' }, false)).toEqual({ kind: 'none' })
    const state = full()
    expect(forSport(state, true)).toBe(state)
  })

  it('searches playlist names without regard to case, and lists all for an empty search', () => {
    const offered = [{ id: 'a', name: 'Hill climbs' }, { id: 'b', name: 'Sprint set' }]
    expect(searchPlaylists(offered, '  HILL ')).toEqual([offered[0]])
    expect(searchPlaylists(offered, '')).toBe(offered)
    expect(searchPlaylists(offered, 'zzz')).toEqual([])
  })
})
