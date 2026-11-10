import { afterEach, describe, expect, it, vi } from 'vitest'
import { LOCALES } from '../i18n/locales'
import { resetCatalogues } from '../i18n/t'
import { LISTED_PATHS } from '../publicSite/publicPaths'
import { prerenderAll } from './prerenderAll'
import * as renderModule from './renderPage'

// The whole prerender runs here on the real pages: no network, as the netless setup guards.

afterEach(() => {
  resetCatalogues()
})

describe('prerenderAll', () => {
  it('renders every listed path in every language with one heading and no inline style', async () => {
    const pages = await prerenderAll()
    expect(Object.keys(pages)).toEqual([...LOCALES])
    for (const lang of LOCALES) {
      expect(Object.keys(pages[lang] ?? {})).toEqual([...LISTED_PATHS])
      for (const path of LISTED_PATHS) {
        const page = pages[lang]?.[path]
        expect(page?.title).toMatch(/classbeat/)
        expect(page?.description.length).toBeGreaterThan(20)
        expect(page?.body).toMatch(/^<div>/)
        expect(page?.body).not.toMatch(/\sstyle=/)
      }
    }
  })

  it('writes the title and description from the catalogue, verbatim', async () => {
    const pages = await prerenderAll()
    expect(pages.en?.['']?.title).toBe('classbeat | Your class, timed to the music')
    expect(pages.sv?.['/privacy']?.title).toBe('Integritet | classbeat')
    expect(pages.da?.['/terms']?.description).toBe(
      'Vilkårene for classbeat: hvad du må, hvad vi gør, og hvad vi ikke lover.',
    )
  })

  it('renders the language of each page, not English', async () => {
    const pages = await prerenderAll()
    expect(pages.sv?.['']?.body).toContain('Ditt pass, i takt med musiken.')
    expect(pages.da?.['/privacy']?.body).toContain('Privatlivspolitik')
    expect(pages.sv?.['']?.body).toContain('href="/sv/signup"')
  })

  it('records the catalogue keys each page rendered', async () => {
    const pages = await prerenderAll()
    const landing = pages.en?.['']?.keys ?? []
    expect(landing).toEqual([...landing].sort())
    expect(landing).toEqual(expect.arrayContaining(['landing.title', 'meta.landing.title', 'meta.landing.description']))
    expect(landing).not.toContain('privacy.title')
    expect(pages.sv?.['/privacy']?.keys).toContain('privacy.title')
  })

  it('makes fetch throw while it renders and restores it after', async () => {
    const real = globalThis.fetch
    vi.spyOn(renderModule, 'renderPage').mockImplementation(() => {
      void fetch('/api/anything')
      return Promise.reject(new Error('unreachable'))
    })
    await expect(prerenderAll()).rejects.toThrow(/fetch was called during the prerender/)
    expect(globalThis.fetch).toBe(real)
  })

  it('fails the build on a throw, naming the page', async () => {
    vi.spyOn(renderModule, 'renderPage').mockRejectedValue(new Error('boom'))
    await expect(prerenderAll()).rejects.toThrow(/en\/: render failed: boom/)
  })

  it('fails on a page without exactly one h1, an inline style, or an untranslated published body', async () => {
    const page = (body: string) => ({ title: 't', description: 'd', body, keys: [] })
    vi.spyOn(renderModule, 'renderPage').mockImplementation((lang) =>
      Promise.resolve(page(lang === 'en' ? '<h2>x</h2><i style="a:b"></i>' : '<h1>x</h1><h1>y</h1>')),
    )
    const error = await prerenderAll().then(
      () => '',
      (caught: unknown) => String(caught),
    )
    expect(error).toMatch(/en\/: expected exactly one <h1>, found 0/)
    expect(error).toMatch(/en\/privacy: an element has a style attribute/)
    expect(error).toMatch(/sv\/terms: expected exactly one <h1>, found 2/)
  })

  it('fails when a published sv or da body equals the English one', async () => {
    vi.spyOn(renderModule, 'renderPage').mockImplementation((lang, path) =>
      Promise.resolve({ title: 't', description: 'd', body: `<h1>same</h1><a href="/${lang}${path}">x</a>`, keys: [] }),
    )
    const error = await prerenderAll().then(
      () => '',
      (caught: unknown) => String(caught),
    )
    expect(error).toMatch(/sv\/privacy: the sv body equals the English body/)
    expect(error).toMatch(/da\/: the da body equals the English body/)
    expect(error).not.toMatch(/en\/: the/)
  })
})
