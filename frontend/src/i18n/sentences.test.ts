import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { decodeSentence, sentenceText } from '../api/sentences'
import { spotifySentence } from '../api/spotifyMessages'
import { en } from './en'
import { translate, type TFunction } from './t'

// The backend writes its sentences as codes (ADR 0023 section 6); this file is its list, written by
// `backend/tests/sentence_cases.py` and checked there. Every code needs an English entry that
// gives the backend's text for the same numbers.
interface Case {
  code: string
  params: Record<string, number>
  english: string
}
const cases = JSON.parse(
  readFileSync(join(import.meta.dirname, '../../../backend/tests/fixtures/sentence_codes.json'), 'utf8'),
) as Case[]
const t: TFunction = (key, params) => translate('en', key, params)

describe('server sentence codes', () => {
  it('has the backend list', () => {
    expect(cases.length).toBeGreaterThan(30)
  })

  it.each(cases.map((c) => [c.code, c] as const))(
    '%s: the English entry equals the server text',
    (_code, c) => {
      expect(sentenceText(t, { code: c.code, params: c.params }, 'server text')).toBe(c.english)
    },
  )

  it('has no sentence entry the backend does not send', () => {
    const sent = new Set(cases.map((c) => `sentence.${c.code}`))
    const mine = Object.keys(en).filter((key) => key.startsWith('sentence.'))
    expect(mine.filter((key) => !sent.has(key))).toEqual([])
  })

  it('shows the server text for a code it does not know, and for none', () => {
    expect(sentenceText(t, { code: 'job.brand_new', params: {} }, 'The server says so.')).toBe(
      'The server says so.',
    )
    expect(sentenceText(t, { code: '__proto__', params: {} }, 'x')).toBe('x')
    expect(sentenceText(t, { code: 'constructor', params: {} }, 'x')).toBe('x')
    expect(sentenceText(t, null, 'plain')).toBe('plain')
    expect(sentenceText(t, null, null)).toBeNull()
  })

  it('decodes a code with whole-number params and drops anything else', () => {
    expect(decodeSentence({ s: 'spotify.rate_limited', p: { count: 3, x: 'a', y: 1.5 } }, 's', 'p')).toEqual({
      code: 'spotify.rate_limited',
      params: { count: 3 },
    })
    expect(decodeSentence({ s: null }, 's', 'p')).toBeNull()
    expect(decodeSentence({}, 's', 'p')).toBeNull()
    expect(decodeSentence({ s: 'job.pool_busy', p: 'nope' }, 's', 'p')).toEqual({
      code: 'job.pool_busy',
      params: {},
    })
  })

  it('words the Spotify categories from the same entries', () => {
    expect(spotifySentence('rate_limited', 150)).toBe(
      'Spotify asks to slow down; trying again in about 3 minutes.',
    )
    expect(spotifySentence('rate_limited')).toBe('Spotify asks to slow down; trying again in about 1 minute.')
    expect(spotifySentence('quota_exhausted', 7200)).toContain('about 2 hours.')
    expect(spotifySentence('nothing')).toBe('Something went wrong with Spotify. Try again later.')
    expect(spotifySentence('constructor')).toBe('Something went wrong with Spotify. Try again later.')
  })
})
