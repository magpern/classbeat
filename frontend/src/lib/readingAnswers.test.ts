import { describe, expect, it } from 'vitest'
import type { ReadingQuestion } from '../api/readingExtras'
import { addPlaylist, NO_TAGS } from '../components/start/musicTags'
import { asksMusic, changed, incomplete, initialAnswers, rereadBody, type ConfirmAnswers } from './readingAnswers'

const LENGTH: ReadingQuestion = { topic: 'length', text: 'How long?', options: ['30 minutes', '45 minutes', '60 minutes'], assumedIndex: 1, noMusicIndex: null }
const ASSUMED_MUSIC: ReadingQuestion = { topic: 'music', text: 'Music?', options: ['Pop', 'Rock', 'No music'], assumedIndex: 0, noMusicIndex: 2 }
const REQUIRED_MUSIC: ReadingQuestion = { ...ASSUMED_MUSIC, assumedIndex: null }
const CLOSED: ReadingQuestion = {
  topic: 'music',
  code: 'which_music',
  text: '',
  options: ['my_playlists', 'no_music'],
  assumedIndex: null,
  noMusicIndex: 1,
}
const READING = 'a1111111-1111-4111-8111-111111111111'
const PLAYLIST = { id: '0123456789abcdefABCDEF', name: 'Tuesday' }

function answering(questions: readonly ReadingQuestion[], more: Partial<ConfirmAnswers> = {}): ConfirmAnswers {
  return { ...initialAnswers(questions), ...more }
}

describe('the answers of the confirm screen', () => {
  it('starts on the assumed answers, and a required question has none', () => {
    const answers = initialAnswers([LENGTH, REQUIRED_MUSIC])
    expect(answers.questions).toEqual([
      { option: 1, text: '' },
      { option: null, text: '' },
    ])
  })

  it('has nothing changed until an answer, a correction or "No music" differs', () => {
    const qs = [LENGTH, ASSUMED_MUSIC]
    expect(changed(qs, initialAnswers(qs))).toBe(false)
    expect(changed(qs, answering(qs, { note: '  ' }))).toBe(false)
    expect(changed(qs, answering(qs, { note: 'Shorter climbs' }))).toBe(true)
    expect(changed(qs, answering(qs, { noMusic: true }))).toBe(true)
    expect(changed(qs, answering(qs, { questions: [{ option: 2, text: '' }, { option: 0, text: '' }] }))).toBe(true)
    expect(changed(qs, answering(qs, { questions: [{ option: 1, text: '' }, { option: 0, text: 'x' }] }))).toBe(true)
  })

  it('blocks a required question that is not answered, by an option or by words', () => {
    const qs = [REQUIRED_MUSIC]
    expect(incomplete(qs, initialAnswers(qs))).toBe(true)
    expect(incomplete(qs, answering(qs, { questions: [{ option: null, text: '   ' }] }))).toBe(true)
    expect(incomplete(qs, answering(qs, { questions: [{ option: 1, text: '' }] }))).toBe(false)
    expect(incomplete(qs, answering(qs, { questions: [{ option: null, text: '80s rock' }] }))).toBe(false)
    expect(incomplete([LENGTH], initialAnswers([LENGTH]))).toBe(false) // an assumed question is answered
  })

  it('blocks "Use one of my playlists" until a playlist is picked', () => {
    const picked = answering([CLOSED], { questions: [{ option: 0, text: '' }] })
    expect(incomplete([CLOSED], picked)).toBe(true)
    expect(incomplete([CLOSED], { ...picked, playlists: addPlaylist(NO_TAGS, PLAYLIST) })).toBe(false)
  })

  it('sends only the answers that differ from what was assumed, and the typed words as text', () => {
    const qs = [LENGTH, ASSUMED_MUSIC, REQUIRED_MUSIC]
    const body = rereadBody(READING, qs, {
      ...initialAnswers(qs),
      questions: [
        { option: 2, text: '' },
        { option: 0, text: '' }, // the assumed one: not sent
        { option: null, text: ' 80s rock ' },
      ],
      note: ' Keep the climbs short ',
    })
    expect(body).toEqual({
      interpretJobId: READING,
      answers: [
        { questionIndex: 0, optionIndex: 2 },
        { questionIndex: 2, text: '80s rock' },
      ],
      note: 'Keep the climbs short',
    })
  })

  it('sends no_music only for a reading with no music question, and playlists only with their answer', () => {
    expect(rereadBody(READING, [LENGTH], answering([LENGTH], { noMusic: true }))).toMatchObject({ noMusic: true })
    expect(asksMusic([LENGTH])).toBe(false)
    expect(rereadBody(READING, [ASSUMED_MUSIC], answering([ASSUMED_MUSIC], { noMusic: true }))).not.toHaveProperty('noMusic')
    const tags = addPlaylist(NO_TAGS, PLAYLIST)
    const picked = rereadBody(READING, [CLOSED], answering([CLOSED], { questions: [{ option: 0, text: '' }], playlists: tags }))
    expect(picked).toMatchObject({ answers: [{ questionIndex: 0, optionIndex: 0 }], musicSources: [PLAYLIST.id] })
    const dropped = rereadBody(READING, [CLOSED], answering([CLOSED], { questions: [{ option: 1, text: '' }], playlists: tags }))
    expect(dropped).not.toHaveProperty('musicSources')
  })
})
