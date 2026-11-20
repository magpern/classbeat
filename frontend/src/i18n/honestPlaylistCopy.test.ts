import { describe, expect, it } from 'vitest'
import da from './da'
import { en } from './en'
import sv from './sv'

// Roadmap Amendment 30 (#1287, superseding the "asked for private" part of Amendment 27): a playlist
// classbeat creates is public from the start, so MOWL can list it. No Spotify playlist text may promise or
// ask for private; each says it is (or may be) public.
const PUBLIC_WORD = { en: /public/i, sv: /offentlig/i, da: /offentlig/i } as const
const catalogues = { en, sv, da } as const
const text = (message: unknown): string => (typeof message === 'string' ? message : JSON.stringify(message))
const KEYS = [
  'songs.approval.privacy',
  'consent.revokeDetails',
  'studio.out.spotify_playlist.desc',
  'spot.profileHint',
] as const
// MOWL lists only playlists on the user's Spotify profile, and the Web API has no "Add to profile" call
// beyond `public` (#1287): the hint and the mismatch detail tell the user what to do in Spotify.
const PROFILE_WORD = { en: /Add to profile/, sv: /Lägg till i profil/, da: /Føj til profil/ } as const
const PROFILE_KEYS = ['spot.profileHint', 'songs.failure.playlist_mismatch.detail'] as const
const SETTING_WORD = { en: /settings/, sv: /inställningar/, da: /indstillinger/ } as const
const ASK_PRIVATE = { en: /ask (Spotify )?for private/i, sv: /ber (Spotify )?om privat/i, da: /beder (Spotify )?om privat/i } as const

describe('honest Spotify playlist privacy copy', () => {
  it('says in every language that the playlist may be public', () => {
    for (const lang of ['en', 'sv', 'da'] as const) {
      const catalogue = catalogues[lang]
      for (const key of KEYS) {
        expect(text(catalogue[key]), `${lang} ${key}`).toMatch(PUBLIC_WORD[lang])
      }
    }
  })

  it('tells the user how to put a playlist on the Spotify profile', () => {
    for (const lang of ['en', 'sv', 'da'] as const) {
      for (const key of PROFILE_KEYS) {
        expect(text(catalogues[lang][key]), `${lang} ${key}`).toMatch(PROFILE_WORD[lang])
      }
      expect(text(catalogues[lang]['sentence.job.playlist_mismatch']), lang).toMatch(/profil/i)
      expect(text(catalogues[lang]['spot.profileSetting']), lang).toMatch(SETTING_WORD[lang])
    }
  })

  it('never says a Spotify playlist is asked for private', () => {
    for (const lang of ['en', 'sv', 'da'] as const) {
      for (const [key, message] of Object.entries(catalogues[lang])) {
        expect(text(message), `${lang} ${key}`).not.toMatch(ASK_PRIVATE[lang])
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
