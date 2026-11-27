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

  it('adds the running sport chosen by hand, and leaves out an unknown Spotify connection', () => {
    const input = composerInput(request({ sport: 'running', sportByHand: true, spotifyConnectionId: undefined }))
    expect(input.sport_override).toBe('running')
    expect(input).not.toHaveProperty('spotify_connection_id')
    expect(composerInput(request())).not.toHaveProperty('sport_override')
  })

  it('sends no sport for Running the words chose: the reading decides (#1558)', () => {
    expect(composerInput(request({ sport: 'running', sportByHand: false }))).not.toHaveProperty('sport_override')
    expect(composerInput(request({ sport: 'running' }))).not.toHaveProperty('sport_override')
  })

  it('sends a sport chosen by hand, Cycling included (#1544: a manual choice always wins)', () => {
    expect(composerInput(request({ sport: 'cycling', sportByHand: true })).sport_override).toBe('cycling')
    expect(composerInput(request({ sport: 'running', sportByHand: true })).sport_override).toBe('running')
    expect(composerInput(request({ sport: 'cycling', sportByHand: false }))).not.toHaveProperty('sport_override')
  })
})

describe('the composer first state', () => {
  const base = { exampleWords: null, offered: [A, B], noMusicOffered: true }
  const auto = (sport: 'cycling' | 'running') => ({ sport, manual: false })

  it('is empty without a kept request or an example', () => {
    expect(initialDraft({ ...base, kept: null })).toEqual({ request: '', tags: NO_TAGS, sport: auto('cycling') })
  })

  it('starts from an example\'s words, the sport following them', () => {
    expect(initialDraft({ ...base, kept: null, exampleWords: 'An example.' }).request).toBe('An example.')
    expect(initialDraft({ ...base, kept: null, exampleWords: 'A 30 minute run' }).sport).toEqual(auto('running'))
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

  it('keeps "No music" only where it is offered, and a kept Running always (no flag since #1544)', () => {
    const kept = { request: 'Quiet', music: 'none', sport: 'running' as const }
    expect(initialDraft({ ...base, kept: { ...kept, sport: 'cycling' } }).tags).toEqual({ kind: 'none' })
    expect(initialDraft({ ...base, kept: { ...kept, sport: 'cycling' }, noMusicOffered: false }).tags).toEqual(NO_TAGS)
    const running = initialDraft({ ...base, kept })
    // The words name no run, so the kept Running was chosen by hand: it stays so.
    expect(running.sport).toEqual({ sport: 'running', manual: true })
    expect(running.tags).toEqual(NO_TAGS)
    expect(initialDraft({ ...base, kept: { ...kept, request: 'A quiet run' } }).sport).toEqual(auto('running'))
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
