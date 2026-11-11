import { afterEach, describe, expect, it } from 'vitest'
import { resetCatalogues } from '../i18n/t'
import { renderPage } from '../prerender/renderPage'
import { checkHeading, checkNoInlineStyle } from './prerenderChecks'
import { DRAFT_PATHS, PUBLISHED_PATHS } from './publicPaths'
import { PRICING_PLANS, SAME_ON_EVERY_PLAN } from './pricing'
import { hasSongPickingNote, mentionsSongPicking, textOf } from './songPickingScan'

// M7a-5 (#950): the Plans page is a draft, passes the prerender checks and shows no price. A price is
// read only for a tester or an admin (ADR 0031 section 7), never on a public page.

afterEach(() => {
  resetCatalogues()
})

// A currency sign or code, or the word price, cost or pay: a figure or a promise of one.
const PRICE = /[$€£¥]|\b(?:USD|EUR|SEK|DKK|GBP|NOK)\b|\bkr\b|\bpric|\bcost|\bpay\b|\bfree trial\b/i

describe('the Plans page prerender', () => {
  it('is a draft: listed, not published', () => {
    expect([...DRAFT_PATHS]).toContain('/pricing')
    expect(PUBLISHED_PATHS).not.toContain('/pricing')
  })

  it.each(['en', 'sv', 'da'] as const)('passes the build checks in %s', async (lang) => {
    const page = await renderPage(lang, '/pricing')
    expect(checkHeading(page.body)).toEqual([])
    expect(checkNoInlineStyle(page.body)).toEqual([])
    expect(page.title).toContain('classbeat')
  })

  it('names the three plans, their quality and what is the same on every plan', async () => {
    const page = await renderPage('en', '/pricing')
    const body = textOf(page.body)
    for (const name of ['Free', 'Individual', 'Professional']) expect(body).toContain(name)
    for (const quality of ['Standard', 'Better', 'Best']) expect(body).toContain(`Design quality: ${quality}`)
    expect(PRICING_PLANS.map((p) => p.id)).toEqual(['free', 'instructor', 'pro'])
    expect(body).toContain('Races')
    expect(body).toContain('Sign-up is by invitation.')
    expect(SAME_ON_EVERY_PLAN.length).toBeGreaterThan(2)
  })

  it('contains no price, in the title, the description or the body', async () => {
    const page = await renderPage('en', '/pricing')
    const visible = `${page.title}. ${page.description}. ${textOf(page.body)}`
    expect(visible).not.toMatch(PRICE)
    expect(visible).not.toMatch(/\d+[.,]\d{2}\b/)
  })

  it('holds no plan number before takeover: the rows are there, with dashes', async () => {
    const page = await renderPage('en', '/pricing')
    const body = textOf(page.body)
    expect(body).not.toMatch(/\d/)
    expect((page.body.match(/data-testid="plan-facts"/g) ?? []).length).toBe(3)
    expect(body).toContain('Classes a month –')
  })

  it('says what song picking needs wherever it mentions it', async () => {
    const page = await renderPage('en', '/pricing')
    const visible = `${page.title}. ${page.description}. ${textOf(page.body)}`
    expect(mentionsSongPicking(visible)).toBe(true)
    expect(hasSongPickingNote(visible)).toBe(true)
  })
})
