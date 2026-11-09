import { describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'

// ADR 0028 section 2 for the class and song screens: button 1 to 3 words, label at most 4, heading
// at most 8, helper at most 12, error at most 20, confirmation at most 25; "classbeat" is not the
// subject of an app-screen sentence. In en, sv and da. The class.rule.* sentences are mirrored from
// the backend (patternNotes.test.ts) and are not covered here.

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

/** Fixed keys with their own limit: what a person reads as a button, a label or a heading. */
const LIMITS: readonly [CatalogueKey, number][] = [
  // buttons
  ['class.doItAnyway', 3],
  ['class.checkAgain', 3],
  ['class.tryAgain', 3],
  ['class.delete.confirm', 3],
  ['class.delete.anyway', 3],
  ['class.delete.keep', 3],
  ['class.save.runSave', 3],
  ['class.save.runUpdate', 3],
  ['class.save.asNew', 3],
  ['class.save.updateAnyway', 3],
  ['class.save.updateButton', 3],
  ['class.save.replace', 3],
  ['class.save.again', 3],
  ['class.save.updateAgain', 3],
  ['class.beat.apply', 3],
  ['class.attach.addAgain', 3],
  ['class.list.design', 3],
  ['songs.uncertain.checkAgain', 3],
  ['songs.uncertain.doAnyway', 3],
  ['songs.uncertain.confirmYes', 3],
  // labels and chips
  ['class.status.saved', 4],
  ['class.status.unsavedChanges', 4],
  ['class.status.notSaved', 4],
  ['class.status.noVersion', 4],
  ['class.notes.currentRules', 4],
  ['class.write.notSaved', 4],
  ['class.write.notUpdated', 4],
  ['class.chat.changed', 4],
  ['class.chat.changedVersion', 4],
  ['class.page.job.progress', 4],
  ['class.goToAccount', 4],
  ['songs.ended.expiredApproved', 4],
  ['songs.ended.expiredSongs', 4],
  ['cred.email.title', 4],
  // headings
  ['class.attach.title', 8],
  ['class.write.title.save', 8],
  ['class.write.title.update', 8],
  ['songs.uncertain.title', 8],
  ['songs.failure.account_changed.title', 8],
  ['songs.failure.rate_limited.title', 8],
  ['songs.failure.scope_mismatch.title', 8],
  // helpers
  ['class.notes.intro', 12],
  ['class.chat.hint', 12],
  ['class.chat.empty', 12],
  ['class.list.noneText', 12],
  ['songs.change.intro', 12],
  // errors and confirmations the pass shortened
  ['class.chat.unavailable', 20],
  ['class.history.cannotChange', 20],
  ['class.updatedUnverified', 20],
  ['cred.email.sent', 20],
]

/** Every key of these groups is an error message: at most 20 words. */
const ERROR_PREFIXES = [
  'class.reason.',
  'class.saveReason.',
  'class.saveFailure.',
  'class.duplicate.reason.',
  'songs.start.',
  'songs.failure.',
] as const

/** The screens this pass covers: all class.* but the mirrored rules, all songs.*, and the email-change notice. */
const inScope = (key: string) =>
  (key.startsWith('class.') && !key.startsWith('class.rule.')) || key.startsWith('songs.') || key.startsWith('cred.email.')

const SCOPE_KEYS = (Object.keys(en) as CatalogueKey[]).filter(inScope)
const ERROR_KEYS = SCOPE_KEYS.filter((k) => ERROR_PREFIXES.some((p) => k.startsWith(p)))

/** Where the product name may appear in these screens: as a place ("from classbeat"), never as a subject. */
const NAME_ALLOWED: readonly CatalogueKey[] = ['class.page.deleted', 'class.delete.warning']

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

describe('class and song screen copy', () => {
  it('is not checked against an empty set of keys', () => {
    // A renamed or removed key must fail here instead of silently dropping out of the guard.
    for (const [key] of LIMITS) expect(en[key], key).toBeTypeOf('string')
    expect(SCOPE_KEYS.length).toBeGreaterThan(400)
    expect(ERROR_KEYS.length).toBeGreaterThan(80)
    expect(ERROR_KEYS.some((k) => k.startsWith('songs.failure.'))).toBe(true)
    expect(SCOPE_KEYS.some((k) => k.startsWith('songs.'))).toBe(true)
    expect(SCOPE_KEYS.some((k) => k.startsWith('class.rule.'))).toBe(false)
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

  it('keeps the shortened text pinned (a limit that is never reached would pass any text)', () => {
    // The checks above are upper bounds. These longest entries prove the counter counts words.
    expect(longest(en, 'class.save.confirmUpdateSoundless')).toBeGreaterThan(20)
    expect(longest(en, 'class.reason.not_changeable')).toBeGreaterThan(15)
    expect(words('one {two} three')).toBe(3)
  })

  it('does not make the product name the subject of a sentence', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE_KEYS) {
        const text = textOf(catalogue, key)
        if (NAME_ALLOWED.includes(key)) {
          expect(text, `${lang} ${key}`).not.toMatch(/(^|[.!?]\s+)classbeat\b/i)
        } else {
          expect(text, `${lang} ${key}`).not.toMatch(/classbeat/i)
        }
      }
    }
  })
})
