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
    { path: 'length_min', question: 'how_long', assumed: 45 },
    { path: 'intensity', question: 'how_hard', assumed: 'moderate' },
    { path: 'intensity', question: 'how_hard', assumed: 'brutal' },
    { path: 'length_min', question: 'How long?', assumed: 45 },
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
        { question: 'how_long', assumed: 45 },
        { question: 'how_hard', assumed: 'moderate' },
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
    expect(screen.getByText('How long should the class be? For now: 45 minutes.')).toBeTruthy()
    expect(screen.getByText('How hard should it be? For now: Moderate.')).toBeTruthy()
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
