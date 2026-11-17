import { describe, expect, it } from 'vitest'
import { composerInput, initialDraft, keptFromRequest, type ComposerRequest } from './composerModel'
import { NO_TAGS, addPlaylist, type MusicTags, type PlaylistTag } from './musicTags'

const A: PlaylistTag = { id: 'AbCdEfGhIjKlMnOpQrStUv', name: 'Hill climbs' }
const B: PlaylistTag = { id: 'ZyXwVuTsRqPoNmLkJiHgFe', name: 'Sprint set' }
const both = addPlaylist(addPlaylist(NO_TAGS, A), B)

const request = (over: Partial<ComposerRequest> = {}): ComposerRequest => ({
  words: '45 minutes, three climbs',
  mowlConnectionId: 'mowl-1',
  spotifyConnectionId: 'spotify-1',
  tags: NO_TAGS,
  sport: 'cycling',
  ...over,
})

describe('the composer request payload', () => {
  it('is the words and the connection ids when nothing is attached', () => {
    expect(composerInput(request())).toEqual({
      request: '45 minutes, three climbs',
      mowl_connection_id: 'mowl-1',
      spotify_connection_id: 'spotify-1',
    })
  })

  it('sends the attached playlists as music_sources, in order, and no music_override', () => {
    const input = composerInput(request({ tags: both }))
    expect(input.music_sources).toEqual([
      { kind: 'spotify_playlist', id: A.id },
      { kind: 'spotify_playlist', id: B.id },
    ])
    expect(input).not.toHaveProperty('music_override')
  })

  it('sends "No music" as music_override and no music_sources', () => {
    const input = composerInput(request({ tags: { kind: 'none' } }))
    expect(input.music_override).toEqual({ source: 'none' })
    expect(input).not.toHaveProperty('music_sources')
  })

  it('adds the running sport, and leaves out an unknown Spotify connection', () => {
    const input = composerInput(request({ sport: 'running', spotifyConnectionId: undefined }))
    expect(input.sport_override).toBe('running')
    expect(input).not.toHaveProperty('spotify_connection_id')
    expect(composerInput(request())).not.toHaveProperty('sport_override')
  })
})

describe('the composer first state', () => {
  const base = { exampleWords: null, offered: [A, B], runningEnabled: true, noMusicOffered: true }

  it('is empty without a kept request or an example', () => {
    expect(initialDraft({ ...base, kept: null })).toEqual({ request: '', tags: NO_TAGS, sport: 'cycling' })
  })

  it('starts from an example\'s words', () => {
    expect(initialDraft({ ...base, kept: null, exampleWords: 'An example.' }).request).toBe('An example.')
  })

  it('restores a kept request with its playlists still on offer, and drops the others', () => {
    const kept = { request: 'Again', music: 'playlist:x', playlists: [B.id, 'gone', A.id], sport: 'cycling' as const }
    const draft = initialDraft({ ...base, kept, exampleWords: 'ignored' })
    expect(draft.request).toBe('Again')
    expect(draft.tags).toEqual({ kind: 'playlists', tags: [B, A] })
  })

  it('reads the single kept playlist key of the old start screen', () => {
    const draft = initialDraft({ ...base, kept: { request: 'Old', music: `playlist:${A.id}`, sport: 'cycling' } })
    expect(draft.tags).toEqual({ kind: 'playlists', tags: [A] })
  })

  it('keeps "No music" only where it is offered, and running only where it is enabled', () => {
    const kept = { request: 'Quiet', music: 'none', sport: 'running' as const }
    expect(initialDraft({ ...base, kept: { ...kept, sport: 'cycling' } }).tags).toEqual({ kind: 'none' })
    expect(initialDraft({ ...base, kept: { ...kept, sport: 'cycling' }, noMusicOffered: false }).tags).toEqual(NO_TAGS)
    const running = initialDraft({ ...base, kept })
    expect(running.sport).toBe('running')
    expect(running.tags).toEqual(NO_TAGS)
    expect(initialDraft({ ...base, kept, runningEnabled: false }).sport).toBe('cycling')
  })

  it('keeps a request in a form it restores from', () => {
    for (const tags of [NO_TAGS, both, { kind: 'none' } as MusicTags]) {
      const kept = keptFromRequest(request({ tags }))
      expect(initialDraft({ ...base, kept }).tags).toEqual(tags)
    }
    expect(keptFromRequest(request({ tags: both }))).toEqual({
      request: '45 minutes, three climbs',
      music: `playlist:${A.id}`,
      playlists: [A.id, B.id],
      sport: 'cycling',
    })
  })
})
