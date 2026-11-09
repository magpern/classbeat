import { describe, expect, it } from 'vitest'
import sv from './sv'
import da from './da'
import { en } from './en'
import type { Catalogue, CatalogueKey } from './en'

// ADR 0028 section 2 for the shell (header, navigation, not-found), the cookie notice, the help page,
// the chrome of the legal pages and the security activity list: button 1 to 3 words, label at most 4,
// heading at most 8, helper at most 12, error at most 20, other text at most 25; "classbeat" is not the
// subject of an app-screen sentence. In en, sv and da. NOT covered: the body of the privacy notice and
// the terms (privacy.*, terms.*, legal.controller.*; legal text, owner and lawyer territory) and the
// sentence.*, class.rule.* and error.reason.* texts (mirrored from the backend, other tests).
//
// The help page is where long explanations live (ADR 0028 section 2), so a help answer is not held to
// 25 words in total; every sentence of it is (25 words), and it names the real buttons and tabs.

const words = (text: string) => text.split(/\s+/).filter((w) => w !== '').length

/** Fixed keys with their own limit: what a person reads as a button, a label, a heading or a helper. */
const LIMITS: readonly [CatalogueKey, number][] = [
  // buttons and links
  ['shell.nav.home', 3],
  ['shell.nav.classes', 3],
  ['shell.nav.new', 3],
  ['shell.nav.music', 3],
  ['shell.nav.account', 3],
  ['shell.signUp', 3],
  ['shell.backHome', 3],
  ['shell.backToStart', 3],
  ['shell.close', 3],
  ['shell.menu.signOut', 3],
  ['shell.admin', 3],
  ['cookies.ok', 3],
  ['help.link', 3],
  ['legal.link.privacy', 3],
  ['legal.link.terms', 3],
  ['activity.more', 3],
  // labels and chips
  ['shell.nav.main', 4],
  ['shell.brandHome', 4],
  ['shell.classesThisMonth', 4],
  ['shell.classesThisMonthLine', 6],
  ['cookies.label', 4],
  ['legal.footer.label', 4],
  ['legal.inline.privacy', 4],
  ['legal.inline.terms', 4],
  ['help.inline.account', 4],
  ['activity.from', 4],
  // headings
  ['shell.notFound', 8],
  ['shell.requireAuth.loadingTitle', 8],
  ['shell.requireAuth.errorTitle', 8],
  ['shell.requireAuth.title', 8],
  ['help.title', 8],
  ['help.contact.title', 8],
  ['activity.title', 8],
  // helpers
  ['shell.notAdmin', 12],
  ['shell.requireAuth.errorTryAgain', 12],
  ['shell.requireAuth.text', 12],
  ['cookies.more', 12],
  ['legal.read', 12],
  ['legal.updated', 12],
  ['activity.intro', 12],
  ['activity.empty', 12],
  ['help.contact.p1', 12],
  ['help.contact.include', 12],
  ['help.contact.li1', 12],
  ['help.contact.li2', 12],
  ['help.contact.li3', 12],
  // errors
  ['activity.loadFailed', 20],
  ['help.contact.none', 20],
  ['help.contact.never', 20],
  // other text
  ['cookies.text', 25],
  ['help.lead', 25],
  ['help.contact.p2', 25],
]

/** Legal chrome (links, headings, helper lines); the legal body is not rewritten here. */
const LEGAL_CHROME: readonly CatalogueKey[] = [
  'legal.link.privacy',
  'legal.link.terms',
  'legal.footer.label',
  'legal.read',
  'legal.inline.privacy',
  'legal.inline.terms',
  'legal.updated',
]

const SCOPE_PREFIXES = ['shell.', 'cookies.', 'help.', 'activity.'] as const
const SCOPE_KEYS = (Object.keys(en) as CatalogueKey[]).filter(
  (k) => SCOPE_PREFIXES.some((p) => k.startsWith(p)) || LEGAL_CHROME.includes(k),
)
const HELP_QUESTIONS = SCOPE_KEYS.filter((k) => k.startsWith('help.') && (k.endsWith('.q') || k.endsWith('.title')))
const HELP_ANSWERS = SCOPE_KEYS.filter((k) => k.startsWith('help.') && k.endsWith('.a'))
const ACTIVITY_KINDS = SCOPE_KEYS.filter((k) => k.startsWith('activity.kind.'))

/** The longest text of an entry (the longest form of a plural); sv and da may omit a key, then English shows. */
const textsOf = (catalogue: Catalogue, key: CatalogueKey): string[] => {
  const message = catalogue[key] ?? en[key]
  return typeof message === 'string' ? [message] : Object.values(message)
}
const longest = (catalogue: Catalogue, key: CatalogueKey): number => Math.max(...textsOf(catalogue, key).map(words))
const textOf = (catalogue: Catalogue, key: CatalogueKey): string => textsOf(catalogue, key).join(' ')
const sentences = (text: string): string[] => text.split(/(?<=[.!?])\s+/).filter((s) => s !== '')

/** The text as a whole word or phrase: "Connect" is not found in "Connections" (nor "Anslut" in "Anslutningar"). */
const wholeWord = (label: string): RegExp =>
  new RegExp(`(?<![\\p{L}\\p{N}])${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\p{L}\\p{N}])`, 'u')

const catalogues = (): [string, Catalogue][] => [['en', en], ['sv', sv], ['da', da]]

/** The buttons and tab names a help answer tells the reader to use: each must appear in the answer. */
const NAMES_IN_ANSWER: readonly [CatalogueKey, readonly CatalogueKey[]][] = [
  ['help.connect.mowl.a', ['account.tab.connections', 'conn.connect']],
  ['help.connect.spotify.a', ['account.tab.connections', 'conn.connect']],
  ['help.connect.swedebeat.a', ['account.tab.connections', 'conn.connect']],
  ['help.design.how.a', []],
  ['help.design.saved.a', ['class.save.title']],
  ['help.design.change.a', ['songs.change.title', 'class.useThisVersion', 'class.save.title']],
  ['help.plan.limits.a', ['account.tab.plan']],
  ['help.plan.code.a', ['account.tab.plan', 'plan.redeemLabel']],
  ['help.signin.reset.a', ['auth.forgot']],
  ['help.signin.methods.a', ['account.tab.security']],
  ['help.signin.twofactor.a', ['account.tab.security']],
  ['help.signin.devices.a', ['account.tab.security']],
  ['help.data.export.a', ['account.tab.data', 'export.button']],
  ['help.data.delete.a', ['account.tab.data', 'delete.button']],
]

/** The removed "I use Swedebeat" switch, in each language. */
const STALE_SWITCH = /I use Swedebeat|Jag använder Swedebeat|Jeg bruger Swedebeat/i

/** The pre-login legal notice names the product once; nothing in this pass is allow-listed. */
const NAME_AS_SUBJECT = /(^|[.!?:;]\s+)classbeat\b/i

/** A sentence that starts with the name, also behind an opening quote or bracket. */
const STARTS_WITH_NAME = /^["'(\u201c\u201e\u00bb]?classbeat\b/i

/**
 * The home link's accessible name is not a sentence: it starts with the visible brand ("classbeat,
 * home"), so a voice-control user who says what they see ("click classbeat") reaches it (WCAG 2.5.3).
 */
const NAME_FIRST_LABELS: readonly CatalogueKey[] = ['shell.brandHome']

describe('shell, cookie notice, help and activity copy', () => {
  it('words the sign-in methods heading as a question in every language', () => {
    for (const lang of [en, sv, da]) expect(lang['help.signin.methods.q']).toMatch(/^(Can|Kan) .*\?$/)
  })

  it('is not checked against an empty set of keys', () => {
    // A renamed or removed key must fail here instead of silently dropping out of the guard.
    for (const [key] of LIMITS) expect(en[key], key).toBeDefined()
    for (const [key, names] of NAMES_IN_ANSWER) for (const k of [key, ...names]) expect(en[k], k).toBeDefined()
    expect(SCOPE_KEYS.length).toBeGreaterThan(90)
    expect(HELP_QUESTIONS.length).toBeGreaterThan(20)
    expect(HELP_ANSWERS.length).toBe(NAMES_IN_ANSWER.length)
    expect(ACTIVITY_KINDS.length).toBeGreaterThan(25)
    expect(SCOPE_KEYS.some((k) => k.startsWith('sentence.'))).toBe(false)
    expect(SCOPE_KEYS.some((k) => k.startsWith('privacy.') || k.startsWith('terms.'))).toBe(false)
  })

  it('keeps buttons, labels, headings and helpers within the ADR 0028 limits in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const [key, limit] of LIMITS) {
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(limit)
      }
    }
  })

  it('keeps the cookie notice short with the link that follows it', () => {
    // The notice shows cookies.text and cookies.more in one paragraph; both are 25 words or fewer
    // each, and the text keeps what it discloses: sign-in cookies, language and theme, the Cloudflare
    // security check on the sign-in pages, and no tracking.
    const disclosed: readonly [string, RegExp][] = [
      ['en', /sign you in.*language and theme.*Cloudflare.*No tracking/s],
      ['sv', /logga in dig.*språk och tema.*Cloudflare.*Ingen spårning/s],
      ['da', /logge dig ind.*sprog og tema.*Cloudflare.*Ingen sporing/s],
    ]
    for (const [lang, catalogue] of catalogues()) {
      expect(textOf(catalogue, 'cookies.text'), `${lang} cookies.text`).toMatch(
        disclosed.find(([l]) => l === lang)?.[1] ?? /^$/,
      )
      expect(longest(catalogue, 'cookies.text') + longest(catalogue, 'cookies.more')).toBeLessThanOrEqual(30)
    }
  })

  it('keeps every help heading and question at 8 words or fewer in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of HELP_QUESTIONS) {
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(8)
      }
    }
  })

  it('keeps every sentence of a help answer at 25 words or fewer in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of HELP_ANSWERS) {
        for (const sentence of sentences(textOf(catalogue, key))) {
          expect(words(sentence), `${lang} ${key}: ${sentence}`).toBeLessThanOrEqual(25)
        }
      }
    }
  })

  it('keeps every security activity line at 14 words or fewer in every language', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of ACTIVITY_KINDS) {
        expect(longest(catalogue, key), `${lang} ${key}`).toBeLessThanOrEqual(14)
      }
    }
  })

  it('names the real buttons and tabs in every help answer', () => {
    // The help page once told readers to turn on a switch that was removed. Each answer now has to
    // contain the visible name of what it tells the reader to press or open, in the reader's language.
    for (const [lang, catalogue] of catalogues()) {
      for (const [key, names] of NAMES_IN_ANSWER) {
        for (const name of names) {
          const label = textOf(catalogue, name).replace(/\{\w+\}/g, '').trim()
          expect(textOf(catalogue, key), `${lang} ${key} names "${label}"`).toMatch(wholeWord(label))
        }
      }
    }
  })

  it('no longer mentions the removed "I use Swedebeat" switch', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE_KEYS) {
        expect(textOf(catalogue, key), `${lang} ${key}`).not.toMatch(STALE_SWITCH)
      }
    }
    // Swedebeat is optional and connected like the other two accounts.
    expect(en['help.connect.swedebeat.a']).toMatch(/optional/)
    expect(sv['help.connect.swedebeat.a']).toMatch(/valfritt/)
    expect(da['help.connect.swedebeat.a']).toMatch(/valgfrit/)
  })

  it('keeps what the help page says is saved and deleted', () => {
    // Nothing safety-relevant was dropped from the disclosures: what is written to Spotify and MOWL,
    // what a deletion removes, and what stays behind.
    expect(en['help.design.saved.a']).toMatch(/private playlist.*Spotify.*MOWL.*private.*Save to MOWL/s)
    expect(en['help.data.delete.a']).toMatch(/cannot be undone.*MOWL.*Spotify stay there/s)
    expect(sv['help.data.delete.a']).toMatch(/Det går inte att ångra.*MOWL.*Spotify finns kvar/s)
    expect(da['help.data.delete.a']).toMatch(/Det kan ikke fortrydes.*MOWL.*Spotify bliver dér/s)
    expect(en['help.connect.mowl.a']).toMatch(/only the token, encrypted/)
    expect(en['help.connect.spotify.a']).toMatch(/Client Secret/)
  })

  it('keeps the counter honest (a limit that is never reached would pass any text)', () => {
    // The checks above are upper bounds. These entries prove the counters count words and sentences.
    expect(longest(en, 'help.connect.spotify.a')).toBeGreaterThan(50)
    expect(Math.max(...sentences(textOf(en, 'help.connect.spotify.a')).map(words))).toBeGreaterThan(15)
    expect(longest(en, 'cookies.text')).toBeGreaterThan(15)
    expect(words('one {two} three')).toBe(3)
    expect(sentences('One two. Three? Four').length).toBe(3)
  })

  it('does not make the product name the subject of a sentence', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of SCOPE_KEYS.filter((k) => !NAME_FIRST_LABELS.includes(k))) {
        expect(textOf(catalogue, key), `${lang} ${key}`).not.toMatch(NAME_AS_SUBJECT)
        for (const sentence of sentences(textOf(catalogue, key))) {
          expect(sentence, `${lang} ${key}: ${sentence}`).not.toMatch(STARTS_WITH_NAME)
        }
      }
    }
    expect(NAME_AS_SUBJECT.test('classbeat could not tell')).toBe(true)
    expect(NAME_AS_SUBJECT.test('Sent. classbeat picks songs')).toBe(true)
    expect(NAME_AS_SUBJECT.test('Sent.  classbeat picks songs')).toBe(true)
    expect(NAME_AS_SUBJECT.test('Home, classbeat')).toBe(false)
    expect(sentences('Sent. "classbeat picks songs" now.').some((s) => STARTS_WITH_NAME.test(s))).toBe(true)
    expect(sentences('Sent. Open classbeat now.').some((s) => STARTS_WITH_NAME.test(s))).toBe(false)
  })

  it('starts the home link name with the visible brand', () => {
    for (const [lang, catalogue] of catalogues()) {
      for (const key of NAME_FIRST_LABELS) {
        expect(textOf(catalogue, key), `${lang} ${key}`).toMatch(/^classbeat, \S+$/)
      }
    }
  })

  it('names the Connect button as a whole word, not inside Connections', () => {
    // The old check used a plain substring, so "Connections" satisfied "Connect" (and "Anslutningar"
    // satisfied "Anslut"). The matcher itself is pinned here.
    expect(wholeWord('Connect').test('Open Connections and press Connect.')).toBe(true)
    expect(wholeWord('Connect').test('Open Connections.')).toBe(false)
    expect(wholeWord('Anslut').test('Öppna Anslutningar.')).toBe(false)
    expect(wholeWord('Anslut').test('Tryck på Anslut.')).toBe(true)
    expect(wholeWord('Save to MOWL').test('Press Save to MOWL.')).toBe(true)
  })
})
