import { readdirSync, readFileSync } from 'node:fs'
import { join, sep } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { en } from '../i18n/en'
import untranslatedList from '../i18n/untranslated.json'
import { resetCatalogues } from '../i18n/t'
import { DRAFT_ROUTES, draftKeys, type DraftDoc } from '../pages/legalDraft/structure'
import { renderPage } from '../prerender/renderPage'
import { checkHeading, checkNoInlineStyle } from './prerenderChecks'
import { DRAFT_PATHS, isPublished, PUBLISHED_PATHS } from './publicPaths'
import { SITE_LINKS, visibleSiteLinks } from './siteLinks'
import { hasSongPickingNote, mentionsSongPicking, textOf } from './songPickingScan'

// PB-4 (#975, ADR 0032 section 6): terms, privacy, refunds and sub-processors are drafts. They are
// routed and prerendered but stay out of the sitemap (published paths only) and out of navigation.

const untranslated: string[] = untranslatedList
const DOCS = Object.keys(DRAFT_ROUTES) as DraftDoc[]
const PATHS = Object.values(DRAFT_ROUTES)

afterEach(() => {
  resetCatalogues()
})

describe('the legal drafts stay drafts', () => {
  it('are listed as draft paths and never as published ones', () => {
    for (const path of PATHS) {
      expect([...DRAFT_PATHS], path).toContain(path)
      expect(PUBLISHED_PATHS, path).not.toContain(path)
      expect(isPublished(path), path).toBe(false)
    }
  })

  it('leave the sitemap alone: the sitemap lists published paths only, so no draft is in it', () => {
    // The backend builds the sitemap from `published` alone (spa_routes.py); the two lists must not overlap.
    expect(PUBLISHED_PATHS.filter((path) => PATHS.includes(path))).toEqual([])
    expect(PUBLISHED_PATHS).toHaveLength(11)
  })

  it('are not in the site links of the header and footer', () => {
    expect(SITE_LINKS.map((link) => link.path).filter((path) => PATHS.includes(path))).toEqual([])
    expect(visibleSiteLinks().map((link) => link.path).filter((path) => PATHS.includes(path))).toEqual([])
  })

  it.each(['en', 'sv', 'da'] as const)('are not linked from any published page in %s', async (lang) => {
    for (const path of PUBLISHED_PATHS) {
      const { body } = await renderPage(lang, path)
      for (const draft of PATHS) {
        expect(body, `${path} links ${draft}`).not.toMatch(new RegExp(`href="[^"]*${draft}(?:["?#])`))
      }
    }
  })

  it('are named by no source file except their own route, meta and structure', () => {
    const srcDir = join(import.meta.dirname, '..')
    const allowed = new Set(['App.tsx', 'publicSite/pageMeta.ts', 'pages/legalDraft/structure.ts'])
    const walk = (dir: string): string[] =>
      readdirSync(join(srcDir, dir), { withFileTypes: true }).flatMap((entry) => {
        const path = dir === '' ? entry.name : `${dir}/${entry.name}`
        if (entry.isDirectory()) return walk(path)
        return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name) ? [path.split(sep).join('/')] : []
      })
    const quoted = PATHS.map((path) => `'${path}'`)
    const offenders = walk('').filter(
      (file) => !allowed.has(file) && quoted.some((q) => readFileSync(join(srcDir, file), 'utf8').includes(q)),
    )
    expect(offenders).toEqual([])
  })
})

describe('the legal draft pages', () => {
  it.each(['en', 'sv', 'da'] as const)('pass the build checks in %s and say they are a draft', async (lang) => {
    for (const path of PATHS) {
      const page = await renderPage(lang, path)
      expect(checkHeading(page.body), path).toEqual([])
      expect(checkNoInlineStyle(page.body), path).toEqual([])
      expect(page.title, path).toContain('classbeat')
      expect(textOf(page.body), path).toContain('This page is a draft')
      expect(page.title.toLowerCase(), path).toContain('draft')
    }
  })

  it('state the no-refund rule as a draft clause (ADR 0031)', async () => {
    const body = textOf((await renderPage('en', '/refunds')).body)
    expect(body).toContain('Draft clause: no refunds')
    expect(body).toContain('Money paid for a plan period is not paid back')
    expect(body).toContain('nothing is ever paid back')
  })

  it('list the processors the code and the decision records name', async () => {
    const body = textOf((await renderPage('en', '/sub-processors')).body)
    for (const name of ['Anthropic', 'MOWL', 'Spotify', 'Swedebeat', 'Google', 'GitHub', 'Microsoft', 'Cloudflare Turnstile', 'Paddle', 'merchant of record']) {
      expect(body, name).toContain(name)
    }
    expect(body).toContain('Email')
    expect(body).toContain('Hosting')
    // The code names neither the email nor the hosting provider, so the page leaves both to the owner.
    expect(body.match(/\[Owner to decide\]/g)?.length ?? 0).toBeGreaterThanOrEqual(3)
  })

  it('make no song-picking claim without the note', async () => {
    for (const path of PATHS) {
      const page = await renderPage('en', path)
      const visible = `${page.title}. ${page.description}. ${textOf(page.body)}`
      if (mentionsSongPicking(visible)) expect(hasSongPickingNote(visible), path).toBe(true)
    }
  })

  it('have every key in the English catalogue, and each is on the untranslated list', () => {
    for (const doc of DOCS) {
      for (const key of draftKeys(doc)) {
        expect(en, key).toHaveProperty([key])
        expect(untranslated, key).toContain(key)
      }
    }
    const used = new Set(DOCS.flatMap((doc) => draftKeys(doc)))
    const banner = ['draft.banner.title', 'draft.banner.body']
    const draftOnly = Object.keys(en).filter((key) => (key.startsWith('draft.') || key.startsWith('meta.draft.')) && !banner.includes(key))
    expect(draftOnly.filter((key) => !used.has(key as never))).toEqual([])
    for (const key of banner) expect(untranslated).toContain(key)
  })
})
