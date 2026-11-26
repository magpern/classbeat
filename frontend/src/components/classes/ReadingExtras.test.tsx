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
    { topic: 'music', text: 'What about the music?', options: ['Pick songs for me', 'Use one of my playlists', 'No music'], assumed_index: 0 },
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
        { topic: 'length', text: 'How long should the class be?', options: ['30 minutes', '45 minutes'], assumedIndex: 1 },
        { topic: 'music', text: 'What about the music?', options: ['Pick songs for me', 'Use one of my playlists', 'No music'], assumedIndex: 0 },
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
        { topic: 'music', text: 'What music do you want?', options: ['Pop hits', 'No music'] },
        { topic: 'length', text: 'No assumption allowed?', options: ['a', 'b'] }, // only music may go without one
      ],
    }
    const { questions } = decodeReadingExtras(body)
    expect(questions).toEqual([{ topic: 'music', text: 'What music do you want?', options: ['Pop hits', 'No music'], assumedIndex: null }])
    render(
      <I18nProvider initial="en">
        <RequestBlock request={null} items={[]} extras={decodeReadingExtras(body)} />
      </I18nProvider>,
    )
    expect(screen.getByText('What music do you want?')).toBeTruthy()
    expect(screen.queryByText(/For now/)).toBeNull()
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
