import { describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'

// ADR 0028 section 2 for the account, sign-in, security, plan, usage and connection screens (Spotify,
// MOWL, Swedebeat): button 1 to 3 words, label at most 4, heading at most 8, helper at most 12, error at
// most 20, confirmation or other text at most 25; "classbeat" is not the subject of an app-screen
// sentence. In en, sv and da. The sentence.* texts are mirrored from the backend (sentences.test.ts)
// and are not covered here.

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

/** Fixed keys with their own limit: what a person reads as a button, a label, a heading or a helper. */
const LIMITS: readonly [CatalogueKey, number][] = [
  // buttons
  ['providerEmail.again', 3],
  ['unsub.stopCategory', 3],
  ['unsub.stopAll', 3],
  ['devices.signOutThis', 3],
  ['mowl.yesDisconnect', 3],
  ['mowl.keep', 3],
  ['consent.yesRevoke', 3],
  ['consent.keep', 3],
  ['export.button', 3],
  ['mowlqr.showNew', 3],
  ['mfa.yesRemove', 3],
  ['mfa.setUp', 3],
  ['spot.confirmSwitch', 3],
  ['spot.keepId', 3],
  ['spot.changeId', 3],
  ['spot.disconnectYes', 3],
  ['spot.connectAgain', 3],
  ['swed.anyway', 3],
  ['swed.keepOther', 3],
  ['swed.disconnectYes', 3],
  ['swmatch.approve', 3],
  ['swmatch.replace', 3],
  ['swreconnect.link', 3],
  ['swnext.showAgain', 3],
  ['swnext.design', 3],
  ['swnext.made', 3],
  ['swnext.right', 3],
  ['guard.confirmPasskey', 3],
  ['passkey.add', 3],
  ['passkey.rename', 3],
  ['passkey.signIn', 3],
  ['plan.trial', 3],
  // labels and chips
  ['plan.budgetLabel', 4],
  ['mowl.statusAgain', 4],
  ['mowlqr.alt', 4],
  ['mfa.qrLabel', 4],
  ['mfa.saved', 4],
  ['spot.changeConfirm', 4],
  ['consent.label', 4],
  ['swmatch.missingChip', 4],
  // headings
  ['mowlqr.title', 8],
  ['swnext.title', 8],
  // helpers
  ['language.help', 12],
  ['auth.inviteLead', 12],
  ['confirm.intro', 12],
  ['emailChange.confirmIntro', 12],
  ['reset.linkedHint', 12],
  ['reset.passkeysHint', 12],
  ['guard.oidc', 12],
  ['unsub.intro', 12],
  ['activity.intro', 12],
  ['mailprefs.intro', 12],
  ['mowlqr.how', 12],
  ['mowlqr.intro', 12],
  ['mfa.offText', 12],
  ['mfa.scan', 12],
  ['spot.intro', 12],
  ['spot.step5', 12],
  ['swnext.expiry', 12],
  ['swnext.prefOff', 12],
  ['swmatch.checkUnsure', 12],
  ['passkey.intro', 12],
  ['plan.budgetText', 12],
  ['delete.stays', 12],
  ['spot.step4', 20],
  // errors and confirmations the pass shortened
  ['spot.invalidClientId', 20],
  ['swed.deviceInUse', 25],
  ['oauth.reauth_stale_set_password', 20],
  ['oauth.reauth_stale_both', 20],
  ['error.reason.passkey_busy', 20],
]

/** Every key of these groups is an error message: at most 20 words. */
const ERROR_PREFIXES = [
  'error.',
  'oauth.',
  'mowl.err.',
  'swed.err.',
  'mowlqr.err.',
  'plan.quota.',
  'plan.reason.',
  'plan.busy.',
] as const

/** Prefixes of the screens this pass covers (everything else in the catalogue is another pass). */
const SCOPE_PREFIXES = [
  'account.',
  'auth.',
  'signin.',
  'signup.',
  'reset.',
  'passkey.',
  'mfa.',
  'devices.',
  'activity.',
  'plan.',
  'usage.',
  'spot.',
  'conn.',
  'consent.',
  'swed.',
  'swnext.',
  'swmatch.',
  'swstart.',
  'swreconnect.',
  'mowl.',
  'mowlqr.',
  'oauth.',
  'delete.',
  'export.',
  'mailprefs.',
  'emailChange.',
  'providerEmail.',
  'confirm.',
  'captcha.',
  'unsub.',
  'guard.',
  'error.',
  'language.',
  'common.',
] as const

const SCOPE_KEYS = (Object.keys(en) as CatalogueKey[]).filter((k) => SCOPE_PREFIXES.some((p) => k.startsWith(p)))
const ERROR_KEYS = SCOPE_KEYS.filter((k) => ERROR_PREFIXES.some((p) => k.startsWith(p)))

/** The pre-login legal notice names the product once; everything else must not start a sentence with it. */
const NAME_ALLOWED: readonly CatalogueKey[] = ['auth.legal']

/** The longest text of an entry (the longest form of a plural); sv and da may omit a key, then English shows. */
const longest = (catalogue: Catalogue, key: CatalogueKey): number => {
  const message = catalogue[key] ?? en[key]
  const forms = typeof message === 'string' ? [message] : Object.values(message)
  return Math.max(...forms.map(words))
}

const textOf = (catalogue: Catalogue, key: CatalogueKey): string => {
  const message = catalogue[key] ?? en[key]
  return typeof message === 'string' ? message : Object.values(message).join(' ')
}

const catalogues = (): [string, Catalogue][] => [['en', en], ['sv', sv], ['da', da]]

describe('account, security and connection copy', () => {
  it('is not checked against an empty set of keys', () => {
    // A renamed or removed key must fail here instead of silently dropping out of the guard.
    for (const [key] of LIMITS) expect(en[key], key).toBeDefined()
    expect(SCOPE_KEYS.length).toBeGreaterThan(300)
    expect(ERROR_KEYS.length).toBeGreaterThan(60)
    expect(SCOPE_KEYS.some((k) => k.startsWith('spot.'))).toBe(true)
    expect(SCOPE_KEYS.some((k) => k.startsWith('conn.'))).toBe(true)
    expect(SCOPE_KEYS.some((k) => k.startsWith('swed.'))).toBe(true)
    expect(SCOPE_KEYS.some((k) => k.startsWith('sentence.'))).toBe(false)
    expect(SCOPE_KEYS.some((k) => k.startsWith('class.'))).toBe(false)
  })

  it('keeps buttons, labels, headings and helpers within the ADR 0028 limits in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const [key, limit] of LIMITS) {
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(limit)
      }
    }
  })

  it('keeps every error message at 20 words or fewer in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of ERROR_KEYS) {
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(20)
      }
    }
  })

  it('keeps every text of these screens at 25 words or fewer in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE_KEYS) {
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(25)
      }
    }
  })

  it('splits the long Spotify, consent and delete disclosures into short parts', () => {
    // Nothing safety-relevant was dropped: what is deleted and what stays each has its own entry.
    const parts: readonly CatalogueKey[] = [
      'spot.disconnectText',
      'spot.disconnectDeleted',
      'spot.disconnectKept',
      'spot.disconnectRevoke',
      'spot.changeText',
      'spot.changeDeleted',
      'spot.changeAccount',
      'consent.revokeText',
      'consent.revokeDetails',
      'delete.text',
      'delete.also',
      'delete.stays',
      'mailprefs.securityNote',
    ]
    for (const [lang, catalogue] of catalogues()) {
      for (const key of parts) {
        expect(textOf(catalogue, key), `${lang} ${key}`).not.toBe('')
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(25)
      }
    }
    expect(en['spot.disconnectDeleted']).toMatch(/consent/i)
    expect(en['spot.disconnectKept']).toMatch(/playlists/i)
    expect(en['spot.step4']).toMatch(/Client Secret/)
  })

  it('states the account-switch consequence where the switch is offered and confirmed', () => {
    // ADR 0009 section 7: a different Spotify account forgets the account, so every class loses its
    // playlist link and undecided playlist steps are deleted. This is visible text, not only Details,
    // and the Client ID change text no longer promises that every class keeps its playlist.
    const consequence: readonly [string, RegExp, RegExp][] = [
      ['en', /loses its playlist link/, /undecided playlist steps are deleted/],
      ['sv', /förlorar[^.]*länken till sin spellista/, /oavgjorda spellistesteg raderas/],
      ['da', /mister[^.]*linket til sin playliste/, /uafklarede playlistetrin slettes/],
    ]
    for (const [lang, catalogue] of catalogues()) {
      const [, link, steps] = consequence.find(([l]) => l === lang) ?? []
      for (const key of ['spot.changeAccount', 'spot.confirmHint'] as const) {
        expect(textOf(catalogue, key), `${lang} ${key}`).toMatch(link ?? /^$/)
        expect(textOf(catalogue, key), `${lang} ${key}`).toMatch(steps ?? /^$/)
      }
      expect(textOf(catalogue, 'spot.changeText'), `${lang} spot.changeText`).not.toMatch(
        /keep their playlists|behåller sina spellistor|beholder deres playlister/,
      )
    }
  })

  it('tells the user how to remove our access after Spotify is disconnected', () => {
    expect(en['spot.disconnected']).toMatch(/Apps in your Spotify account settings/)
    expect(sv['spot.disconnected']).toMatch(/Appar i dina Spotify-kontoinställningar/)
    expect(da['spot.disconnected']).toMatch(/Apps i dine Spotify-kontoindstillinger/)
  })

  it('keeps the counter honest (a limit that is never reached would pass any text)', () => {
    // The checks above are upper bounds. These entries prove the counter counts words.
    expect(longest(en, 'spot.disconnectDeleted')).toBeGreaterThan(20)
    expect(longest(en, 'delete.text')).toBeGreaterThan(15)
    expect(words('one {two} three')).toBe(3)
    expect(words('  spaced   out  ')).toBe(2)
  })

  it('does not make the product name the subject of a sentence', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE_KEYS) {
        if (NAME_ALLOWED.includes(key)) continue
        expect(textOf(catalogue, key), `${lang} ${key}`).not.toMatch(/(^|[.!?:;]\s+)classbeat\b/i)
      }
    }
  })
})
