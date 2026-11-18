import { describe, expect, it } from 'vitest'
import da from './da'
import { en } from './en'
import sv from './sv'

// Roadmap Amendment 27 (#1162): a playlist classbeat creates is asked for private, but Spotify may make
// it public. No Spotify playlist text may promise private; each says it may be public.
const PUBLIC_WORD = { en: /public/i, sv: /offentlig/i, da: /offentlig/i } as const
const catalogues = { en, sv, da } as const
const text = (message: unknown): string => (typeof message === 'string' ? message : JSON.stringify(message))
const KEYS = ['songs.approval.privacy', 'consent.revokeDetails', 'studio.out.spotify_playlist.desc'] as const

describe('honest Spotify playlist privacy copy', () => {
  it('says in every language that the playlist may be public', () => {
    for (const lang of ['en', 'sv', 'da'] as const) {
      const catalogue = catalogues[lang]
      for (const key of KEYS) {
        expect(text(catalogue[key]), `${lang} ${key}`).toMatch(PUBLIC_WORD[lang])
      }
    }
  })

  it('never tells the user to make the working playlist private again', () => {
    for (const lang of ['en', 'sv', 'da'] as const) {
      const catalogue = catalogues[lang]
      expect(text(catalogue['sentence.job.pool_unusable']), lang).not.toMatch(/privat/i)
    }
  })
})
