import { describe, expect, it } from 'vitest'
import { decodeComposerState } from './composerState'
import { musicAnswerBody } from './musicAnswer'

describe('the music answer body', () => {
  it('sends only what each choice takes', () => {
    expect(musicAnswerBody({ choice: 'pick' })).toEqual({ choice: 'pick' })
    expect(musicAnswerBody({ choice: 'later' })).toEqual({ choice: 'later' })
    expect(musicAnswerBody({ choice: 'describe', note: ' 80s hits ' })).toEqual({ choice: 'describe', note: '80s hits' })
    expect(musicAnswerBody({ choice: 'playlists', playlistIds: ['a', 'b'] })).toEqual({
      choice: 'playlists',
      playlists: [
        { kind: 'spotify_playlist', id: 'a' },
        { kind: 'spotify_playlist', id: 'b' },
      ],
    })
  })
})

describe('the class answer\'s music decision', () => {
  it('reads the state, the choice and the playlist count', () => {
    expect(decodeComposerState({ music_decision: { state: 'decided', choice: 'playlists', playlists: 2 } }).musicDecision).toEqual({
      state: 'decided',
      choice: 'playlists',
      playlists: 2,
    })
    expect(decodeComposerState({ music_decision: { state: 'undecided', choice: null, playlists: 0 } }).musicDecision?.state).toBe('undecided')
  })

  it('reads none, or an unknown state, as no decision', () => {
    expect(decodeComposerState({}).musicDecision).toBeNull()
    expect(decodeComposerState({ music_decision: null }).musicDecision).toBeNull()
    expect(decodeComposerState({ music_decision: { state: 'maybe' } }).musicDecision).toBeNull()
  })
})
