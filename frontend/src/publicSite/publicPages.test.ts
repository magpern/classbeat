import { afterEach, describe, expect, it } from 'vitest'
import da from '../i18n/da'
import { en, type CatalogueKey } from '../i18n/en'
import sv from '../i18n/sv'
import { resetCatalogues } from '../i18n/t'
import { renderPage } from '../prerender/renderPage'
import { CLASS_LABEL, FEATURE_GROUPS, type Claim } from './claims'
import { CONNECTIONS, STEPS } from './howItWorks'
import { DRAFT_PATHS, LISTED_PATHS } from './publicPaths'
import { hasSongPickingNote, mentionsSongPicking, textOf } from './songPickingScan'

// PP4 (#893): the claim table is data, every claim has a class and a visible note, and no public page
// says classbeat picks songs without saying what that needs.

afterEach(() => {
  resetCatalogues()
})

const CLAIMS: readonly Claim[] = [...FEATURE_GROUPS.flatMap((g) => g.claims), ...CONNECTIONS]
const text = (key: CatalogueKey): string => {
  const message = en[key]
  if (typeof message !== 'string') throw new Error(`${key} must be plain text`)
  return message
}

describe('the song-picking scan', () => {
  it('finds a statement that classbeat picks songs', () => {
    for (const s of [
      'We pick the songs for you.',
      'classbeat chooses songs that fit.',
      'Let us pick the songs',
      'The songs we choose go into a playlist.',
      'Songs are chosen when you design a real class.',
      'The songs picked for you.',
      'Selected songs go to Spotify.',
    ]) {
      expect(mentionsSongPicking(s), s).toBe(true)
    }
    for (const s of ['Låt oss välja låtarna.', 'Låtarna vi väljer åt dig.', 'Lad os vælge sangene.', 'De sange, vi vælger.']) {
      expect(mentionsSongPicking(s), s).toBe(true)
    }
    expect(mentionsSongPicking('Utforma till din egen spellista från MOWL.')).toBe(false)
    expect(mentionsSongPicking('Design til din egen playliste fra MOWL.')).toBe(false)
    expect(mentionsSongPicking('Design to your own playlist from MOWL.')).toBe(false)
    expect(mentionsSongPicking('This example has no music. It shows the shape of a class only.')).toBe(false)
  })

  it('wants one sentence naming Spotify, the Client ID and the plan', () => {
    expect(hasSongPickingNote('Needs a Spotify connection with your own Client ID, and song picking on your plan.')).toBe(true)
    expect(hasSongPickingNote('Needs Spotify. Your own Client ID. A plan.')).toBe(false)
    expect(hasSongPickingNote('Needs a Spotify connection with your own Client ID.')).toBe(false)
    expect(hasSongPickingNote('We pick the songs.')).toBe(false)
  })
})

describe('the claim table', () => {
  it('has unique ids, evidence and catalogue keys for every claim', () => {
    expect(CLAIMS.length).toBeGreaterThan(15)
    expect(new Set(CLAIMS.map((c) => `${c.title}`)).size).toBe(CLAIMS.length)
    for (const claim of CLAIMS) {
      expect(claim.evidence.length, claim.id).toBeGreaterThan(10)
      for (const key of [claim.title, claim.text, claim.note]) expect(typeof en[key], `${claim.id} ${key}`).toBe('string')
      expect(Object.keys(CLASS_LABEL)).toContain(claim.availability)
    }
    for (const step of STEPS) expect(step.evidence.length, step.id).toBeGreaterThan(10)
  })

  it('names the connection of every claim that needs one', () => {
    for (const claim of CLAIMS) {
      if (claim.availability === 'connection') expect(claim.needs.length, claim.id).toBeGreaterThan(0)
    }
    expect(CLAIMS.filter((c) => c.availability === 'available').length).toBeGreaterThan(0)
  })

  it('marks every song-picking claim and gives it the full note', () => {
    const picking = CLAIMS.filter((c) => c.songPicking === true)
    expect(picking.length).toBeGreaterThanOrEqual(2)
    for (const claim of picking) {
      expect(claim.availability, claim.id).toBe('gated')
      expect(claim.needs, claim.id).toContain('spotify')
      expect(hasSongPickingNote(text(claim.note)), claim.id).toBe(true)
    }
  })

  it('says a switched-off feature is available once we switch it on', () => {
    // A claim resting on a switch that defaults to off must say so in its note (review of PP4, #893).
    const switched = CLAIMS.filter((c) => /default off/i.test(c.evidence))
    expect(switched.map((c) => c.id)).toEqual(expect.arrayContaining(['songs', 'swedebeat', 'noMusic', 'running']))
    for (const claim of switched) expect(text(claim.note), claim.id).toMatch(/switch it on/)
  })

  it('marks a claim whose own text says classbeat picks songs', () => {
    for (const claim of CLAIMS) {
      if (mentionsSongPicking(`${text(claim.title)}. ${text(claim.text)}`)) {
        expect(claim.songPicking, claim.id).toBe(true)
      }
    }
  })
})

describe('the new pages are drafts', () => {
  it('lists How it works and Features as drafts, not published', () => {
    expect([...DRAFT_PATHS].slice(0, 3)).toEqual(['/help', '/how-it-works', '/features'])
    expect([...DRAFT_PATHS].slice(3).every((path) => path.startsWith('/examples'))).toBe(true)
  })
})

describe('the rendered public pages', () => {
  it('make no song-picking statement without the Spotify, Client ID and plan note', async () => {
    for (const path of LISTED_PATHS) {
      const page = await renderPage('en', path)
      const visible = `${page.title}. ${page.description}. ${textOf(page.body)}`
      if (mentionsSongPicking(visible)) expect(hasSongPickingNote(visible), path || '/').toBe(true)
    }
  })

  it('shows Features with every claim, its class and its note, and the invitation line', async () => {
    const body = textOf((await renderPage('en', '/features')).body)
    for (const group of FEATURE_GROUPS) {
      for (const claim of group.claims) {
        expect(body, claim.id).toContain(text(claim.title))
        expect(body, claim.id).toContain(text(claim.note))
        expect(body, claim.id).toContain(text(CLASS_LABEL[claim.availability]))
      }
    }
    expect(body).toContain('Join the beta')
    expect(body).toContain('Sign-up is by invitation.')
    expect(body).not.toMatch(/waitlist|wait list/i)
  })

  it('shows How it works with its four steps, the connections and the invitation line', async () => {
    const page = await renderPage('en', '/how-it-works')
    const body = textOf(page.body)
    for (const step of STEPS) expect(body, step.id).toContain(text(step.title))
    for (const row of CONNECTIONS) {
      expect(body, row.id).toContain(text(row.text))
      expect(body, row.id).toContain(text(row.note))
    }
    expect(body).toContain('Sign-up is by invitation.')
    expect(page.body.match(/<h1[ >]/g)).toHaveLength(1)
  })

  it('does not link the draft pages from any page while they are drafts', async () => {
    for (const path of LISTED_PATHS) {
      const { body } = await renderPage('en', path)
      expect(body, path || '/').not.toMatch(/href="\/en\/(how-it-works|features)"/)
    }
  })
})

describe.each([['sv', sv], ['da', da]] as const)('the %s public pages (PP7)', (lang, catalogue) => {
  it('has every claim note and keeps the Spotify, Client ID and plan note on song-picking claims', () => {
    for (const claim of CLAIMS) {
      const note = catalogue[claim.note]
      if (typeof note !== 'string') throw new Error(`${claim.id} note must be plain text`)
      if (claim.songPicking === true) expect(hasSongPickingNote(note), claim.id).toBe(true)
      if (/default off/i.test(claim.evidence)) expect(note, claim.id).toMatch(/aktiver/)
    }
  })

  it('makes no song-picking statement without the note, on any public page', async () => {
    for (const path of [...LISTED_PATHS, ...DRAFT_PATHS]) {
      const page = await renderPage(lang, path)
      const visible = `${page.title}. ${page.description}. ${textOf(page.body)}`
      if (mentionsSongPicking(visible)) expect(hasSongPickingNote(visible), `${lang} ${path || '/'}`).toBe(true)
    }
  })
})
