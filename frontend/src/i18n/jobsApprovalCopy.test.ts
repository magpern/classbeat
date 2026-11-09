import { describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'

// ADR 0028 section 2 for song approval, jobs, errors and New class: button 1 to 3 words,
// label at most 4, heading at most 8, helper at most 12, error at most 20, any other text at most 25;
// "classbeat" is never the subject of an app-screen sentence. In en, sv and da.
// Not covered here: class.* and the songs.start.*, songs.failure.*, songs.uncertain.* texts of the
// class screens (classSongsCopy.test.ts), the sign-in reasons error.reason.* (account area), and the sentence.* texts, whose English the
// backend sends (sentences.test.ts pins it to the server text).

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

const BUTTONS: readonly CatalogueKey[] = [
  'songs.ended.reconnect',
  'songs.ended.createAgain',
  'songs.ended.pickAgain',
  'songs.ended.startNew',
  'songs.ended.backToClass',
  'songs.approval.approve',
  'songs.approval.askChanges',
  'songs.approval.stopAndChange',
  'songs.approval.reload',
  'songs.approval.openClass',
  'songs.approval.refresh',
  'songs.change.marked',
  'songs.change.all',
  'songs.list.replace',
  'job.notFound.designLink',
  'job.signedOut.signIn',
  'job.stop',
  'job.goToAccount',
  'job.ready.openClass',
  'job.ready.designAnother',
  'job.again.design',
  'job.again.startNew',
  'job.again.tryAgain',
  'newclass.side.submit',
  'music.connectSpotify',
  'music.page.connectButton',
]

const LABELS: readonly CatalogueKey[] = [
  'songs.approval.noteLabel',
  'songs.change.different',
  'songs.ended.checking',
  'job.matchStopped',
  'job.stepOf',
  'newclass.formLabel',
  'newclass.className',
  'newclass.lengthMinutes',
  'newclass.side.title',
  'newclass.summary.songsPicked',
  'newclass.summary.noMix',
  'newclass.summary.noPlaylist',
  'newclass.mix.label',
  'newclass.playlist.label',
  'music.openSettings',
  'music.pick',
  'music.swedebeat',
  'music.page.untitled',
]

const HELPERS: readonly CatalogueKey[] = [
  'songs.approval.markHint',
  'songs.approval.privacy',
  'songs.change.writeRequest',
  'songs.change.markSongs',
  'job.waitingForSlot',
  'job.waitingToStart',
  'newclass.notes.hint',
  'newclass.notes.hintNoGames',
  'newclass.sport.runningHint',
  'newclass.side.sketchNote',
  'newclass.mix.hint',
  'newclass.playlist.hint',
  'newclass.style.hint',
  'music.needsSpotify',
  'music.needsConsent',
  'music.pickHint',
  'music.noneHint',
  'music.swedebeatHint',
]

/** Errors and what-happened lines: at most 20 words. */
const ERROR_PREFIXES = [
  'songs.approve.',
  'songs.outcome.',
  'job.revise.',
  'job.retry.',
  'job.again.',
  'newclass.createError.',
  'newclass.playlistError.',
  'newclass.invalid.',
  'error.',
] as const

/** Failure details sit under their title: job.failure.<code>.detail. */
const isFailureDetail = (key: string) => /^job\.failure\..*\.detail$/.test(key)

/** Headings and progress lines: at most 8 words. */
const isHeading = (key: string) =>
  /(\.title|Title)$/.test(key) || /^(job\.(stage|step)|songs\.stage)\./.test(key) || key === 'songs.approval.heading'

/** Texts the class screens' pass (classSongsCopy.test.ts) owns, so this guard leaves them alone. */
const OTHER_PASS_PREFIXES = ['songs.start.', 'songs.failure.', 'songs.uncertain.'] as const
const OTHER_PASS: readonly string[] = ['songs.change.intro', 'songs.ended.expiredApproved', 'songs.ended.expiredSongs']

const inScope = (key: string) =>
  /^(songs|job|newclass|music|error)\./.test(key) &&
  !key.startsWith('error.reason.') &&
  !key.startsWith('sentence.') &&
  !OTHER_PASS_PREFIXES.some((p) => key.startsWith(p)) &&
  !OTHER_PASS.includes(key)

const SCOPE_KEYS = (Object.keys(en) as CatalogueKey[]).filter(inScope)

/** The limit of a key: its kind (button, label, heading, helper, error), else 25 for any other text. */
const limitOf = (key: CatalogueKey): number => {
  if (BUTTONS.includes(key)) return 3
  if (LABELS.includes(key)) return 4
  if (isHeading(key)) return 8
  if (HELPERS.includes(key)) return 12
  if (isFailureDetail(key) || ERROR_PREFIXES.some((p) => key.startsWith(p))) return 20
  return 25
}

/** The longest text of an entry (the longest form of a plural); sv and da may omit a key, then English shows. */
const forms = (catalogue: Catalogue, key: CatalogueKey): string[] => {
  const message = catalogue[key] ?? en[key]
  return typeof message === 'string' ? [message] : Object.values(message)
}
const longest = (catalogue: Catalogue, key: CatalogueKey): number => Math.max(...forms(catalogue, key).map(words))

const catalogues = (): [string, Catalogue][] => [['en', en], ['sv', sv], ['da', da]]

describe('song approval, job, error and New class copy', () => {
  it('is not checked against an empty or stale set of keys', () => {
    // A renamed or removed key must fail here instead of silently dropping out of the guard.
    for (const key of [...BUTTONS, ...LABELS, ...HELPERS]) expect(en[key], key).toBeTypeOf('string')
    expect(SCOPE_KEYS.length).toBeGreaterThan(300)
    for (const prefix of ['songs.', 'job.', 'newclass.', 'music.', 'error.']) {
      expect(SCOPE_KEYS.some((k) => k.startsWith(prefix)), prefix).toBe(true)
    }
    expect(SCOPE_KEYS.filter((k) => limitOf(k) === 20).length).toBeGreaterThan(80)
    expect(SCOPE_KEYS.some((k) => k.startsWith('class.'))).toBe(false)
    expect(SCOPE_KEYS.some((k) => k.startsWith('error.reason.'))).toBe(false)
    expect(SCOPE_KEYS.some((k) => k.startsWith('sentence.'))).toBe(false)
    // The counter counts words, and the limits differ by kind.
    expect(words('one {two} three')).toBe(3)
    expect(limitOf('job.again.design')).toBe(3)
    expect(limitOf('music.openSettings')).toBe(4)
    expect(limitOf('job.failure.music_changed.title')).toBe(8)
    expect(limitOf('newclass.notes.hint')).toBe(12)
    expect(limitOf('job.failure.music_changed.detail')).toBe(20)
    expect(limitOf('job.use.changeSongs')).toBe(25)
  })

  it('keeps every text within the limit of its kind in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE_KEYS) {
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(limitOf(key))
      }
    }
  })

  it('does not make the product name the subject of a sentence', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE_KEYS) {
        expect(forms(catalogue, key).join(' '), `${lang} ${key}`).not.toMatch(/classbeat/i)
      }
    }
  })

  it('keeps the "nothing written", "nothing changed" and "not designed" wording of a sample of the shortened texts', () => {
    const must: [CatalogueKey, RegExp, RegExp, RegExp][] = [
      ['songs.outcome.nothing', /Nothing was written/, /Inget skrevs/, /Intet blev skrevet/],
      ['songs.approve.expired', /Nothing was written/, /Inget skrevs/, /Intet blev skrevet/],
      ['job.failure.upstream_unavailable.detail', /was not designed/, /utformades inte/, /blev ikke designet/],
      ['job.revise.rate_limited', /Nothing changed/, /Inget ändrades/, /Intet blev ændret/],
      ['job.revise.invalid_output', /Nothing changed/, /Inget ändrades/, /Intet blev ændret/],
      ['job.use.newClassForSongs', /nothing changed/, /inget ändrades/, /intet blev ændret/],
    ]
    for (const [key, enRe, svRe, daRe] of must) {
      expect(forms(en, key).join(' '), `en ${key}`).toMatch(enRe)
      expect(forms(sv, key).join(' '), `sv ${key}`).toMatch(svRe)
      expect(forms(da, key).join(' '), `da ${key}`).toMatch(daRe)
    }
  })
})
