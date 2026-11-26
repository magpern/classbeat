import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { decodeReadingExtras, NO_EXTRAS } from '../../api/readingExtras'
import { I18nProvider } from '../../i18n/I18n'
import { RequestBlock } from './RequestBlock'

const BODY = {
  pinned_songs: [
    { title: 'Africa', artist: 'Toto', at: 'the last climb', quote: null },
    { title: 'Mamma Mia', artist: null, at: null },
    { title: '', artist: 'dropped' },
    'not an object',
  ],
  questions: [
    { topic: 'length', text: 'How long should the class be?', options: ['30 minutes', '45 minutes'], assumed_index: 1 },
    { topic: 'music', text: 'What about the music?', options: ['Pick songs for me', 'Use one of my playlists', 'No music'], assumed_index: 0, no_music_index: 2 },
    { topic: 'length', text: 'One option only?', options: ['a'], assumed_index: 0 },
    { topic: 'sport', text: 'Index out of range?', options: ['a', 'b'], assumed_index: 2 },
    { topic: 'nonsense', text: 'Unknown topic?', options: ['a', 'b'], assumed_index: 0 },
    { topic: 'other', text: 'Not strings?', options: ['a', 3], assumed_index: 0 },
  ],
}

describe('the composer part of the reading (ADR 0038 section 3)', () => {
  it('decodes only what checks', () => {
    expect(decodeReadingExtras(BODY)).toEqual({
      pinnedSongs: [
        { title: 'Africa', artist: 'Toto', at: 'the last climb' },
        { title: 'Mamma Mia', artist: null, at: null },
      ],
      questions: [
        { topic: 'length', text: 'How long should the class be?', options: ['30 minutes', '45 minutes'], assumedIndex: 1, noMusicIndex: null },
        { topic: 'music', text: 'What about the music?', options: ['Pick songs for me', 'Use one of my playlists', 'No music'], assumedIndex: 0, noMusicIndex: 2 },
      ],
    })
    expect(decodeReadingExtras({ items: [] })).toEqual(NO_EXTRAS)
  })

  it('shows the pinned songs and the questions with what was assumed', () => {
    render(
      <I18nProvider initial="en">
        <RequestBlock request={null} items={[]} extras={decodeReadingExtras(BODY)} />
      </I18nProvider>,
    )
    expect(screen.getByText('Songs you named')).toBeTruthy()
    expect(screen.getByText('Africa by Toto')).toBeTruthy()
    expect(screen.getByText('Where: the last climb')).toBeTruthy()
    expect(screen.getByText('Mamma Mia')).toBeTruthy()
    expect(screen.getByText('How long should the class be? For now: 45 minutes')).toBeTruthy()
    expect(screen.getByText('What about the music? For now: Pick songs for me')).toBeTruthy()
  })

  it('keeps a required music question that assumes nothing, and shows no assumed answer for it', () => {
    const body = {
      questions: [
        { topic: 'music', text: 'What music do you want?', options: ['Pop hits', 'No music'], no_music_index: 1 },
        { topic: 'length', text: 'No assumption allowed?', options: ['a', 'b'] }, // only music may go without one
      ],
    }
    const { questions } = decodeReadingExtras(body)
    expect(questions).toEqual([{ topic: 'music', text: 'What music do you want?', options: ['Pop hits', 'No music'], assumedIndex: null, noMusicIndex: 1 }])
    render(
      <I18nProvider initial="en">
        <RequestBlock request={null} items={[]} extras={decodeReadingExtras(body)} />
      </I18nProvider>,
    )
    expect(screen.getByText('What music do you want?')).toBeTruthy()
    expect(screen.queryByText(/For now/)).toBeNull()
  })

  it('decodes the closed which_music question and phrases it from the catalogue, never from the body', () => {
    const closed = { topic: 'music', code: 'which_music', options: ['my_playlists', 'no_music'], no_music_index: 1 }
    const body = {
      questions: [
        closed,
        { ...closed, code: 'other_code' }, // an unknown code
        { ...closed, text: 'Model words?' }, // a closed question has no model text
        { ...closed, assumed_index: 0 }, // nor an assumed answer
        { ...closed, options: ['no_music', 'my_playlists'] }, // nor other options
        { ...closed, topic: 'length' },
      ],
    }
    const { questions } = decodeReadingExtras(body)
    expect(questions).toEqual([{ topic: 'music', code: 'which_music', text: '', options: ['my_playlists', 'no_music'], assumedIndex: null, noMusicIndex: 1 }])
    render(
      <I18nProvider initial="en">
        <RequestBlock request={null} items={[]} extras={decodeReadingExtras(body)} />
      </I18nProvider>,
    )
    expect(screen.getByText('What music do you want? Name a style, an era or an artist.')).toBeTruthy()
    expect(screen.queryByText(/my_playlists|which_music|For now/)).toBeNull()
  })

  it.each([
    ['sv', 'Vilken musik vill du ha? Ange en stil, en epok eller en artist.'],
    ['da', 'Hvilken musik vil du have? Angiv en stil, en periode eller en kunstner.'],
  ] as const)('phrases the closed question in %s', async (language, sentence) => {
    const body = { questions: [{ topic: 'music', code: 'which_music', options: ['my_playlists', 'no_music'] }] }
    render(
      <I18nProvider initial={language}>
        <RequestBlock request={null} items={[]} extras={decodeReadingExtras(body)} />
      </I18nProvider>,
    )
    expect(await screen.findByText(sentence)).toBeTruthy()
  })

  it('leaves out a music question that does not mark an offered "No music" (Amendment 37 decision 6)', () => {
    const music = { topic: 'music', text: 'Music?', options: ['Pop', 'Rock', 'No music'] }
    const body = {
      questions: [
        music, // no marker
        { ...music, no_music_index: 3 }, // beyond the options
        { ...music, no_music_index: -1 },
        { ...music, no_music_index: 1.5 },
        { ...music, no_music_index: '2' },
        { ...music, assumed_index: 2, no_music_index: 2 }, // "No music" is never assumed
        { ...music, text: 'Marked?', no_music_index: 2 },
      ],
    }
    expect(decodeReadingExtras(body).questions).toEqual([{ topic: 'music', text: 'Marked?', options: ['Pop', 'Rock', 'No music'], assumedIndex: null, noMusicIndex: 2 }])
  })

  it('shows nothing new for a class without them', () => {
    render(
      <I18nProvider initial="en">
        <RequestBlock request={null} items={[{ path: 'length_min', origin: 'default', reasonCode: null, value: 45, quote: null }]} />
      </I18nProvider>,
    )
    expect(screen.queryByTestId('reading-extras')).toBeNull()
    expect(screen.queryByText('Songs you named')).toBeNull()
  })

  it.each(['sv', 'da'] as const)('has its words in %s', (language) => {
    render(
      <I18nProvider initial={language}>
        <RequestBlock request={null} items={[]} extras={decodeReadingExtras(BODY)} />
      </I18nProvider>,
    )
    expect(screen.getByTestId('reading-extras').textContent).not.toMatch(/interp\./)
  })
})
