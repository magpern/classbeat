import { afterEach, describe, expect, it } from 'vitest'
import { resetCatalogues } from '../i18n/t'
import { prerenderAll } from '../prerender/prerenderAll'
import { EXAMPLES } from './examples'
import raw from './examples.json'
import { LISTED_PATHS } from './publicPaths'

// The examples are made up (ADR 0030, PP5): nothing in the fixture or in a prerendered page may
// look like real data. This is a refusal list, so a new kind of leak is added here, not assumed away.

afterEach(() => {
  resetCatalogues()
})

const FORBIDDEN: [string, RegExp][] = [
  ['a UUID', /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i],
  ['an email address', /[\w.+-]+@[\w-]+\.[a-z]{2,}/i],
  ['an API path', /\/api\//i],
  ['a Spotify URI', /spotify:/i],
  ['a Spotify image host', /i\.scdn\.co/i],
  ['a token-like string', /\b(?:eyJ[\w-]{10,}|sk-[\w-]{16,}|ghp_\w{16,}|xox[bp]-[\w-]{10,}|[A-Za-z0-9_-]{32,})\b/],
  ['a Spotify track id', /\bopen\.spotify\.com\b/i],
  [
    'a real song or artist',
    /britney|spears|backstreet|spice girls|\bn ?sync\b|macarena|wannabe|dua lipa|daft punk|beyonc|madonna|abba|michael jackson|whitney|mariah|shakira|eminem|rihanna|taylor swift|the weeknd|lady gaga|kylie|robyn|avicii|calvin harris|toxic|baby one more time|livin' la vida|mr\. brightside|dancing queen|sweet caroline|don't stop believin/i,
  ],
]

function check(label: string, text: string) {
  for (const [what, pattern] of FORBIDDEN) {
    const hit = pattern.exec(text)
    expect(hit, `${label} holds ${what}: ${hit?.[0] ?? ''}`).toBeNull()
  }
}

describe('example fixtures', () => {
  it('hold no identifier, address, path or real name', () => {
    check('examples.json', JSON.stringify(raw))
  })

  it('name no song, no artist and no tempo', () => {
    for (const e of EXAMPLES) {
      for (const s of e.segments) {
        expect(Object.keys(s).sort()).toEqual(['intervals', 'kind', 'name'])
        for (const i of s.intervals) {
          expect(Object.keys(i)).not.toEqual(expect.arrayContaining(['song']))
          expect(JSON.stringify(i)).not.toMatch(/bpm|spotify|track|artist/i)
        }
      }
    }
  })
})

describe('prerendered pages', () => {
  it('hold none of it, in any language', async () => {
    const pages = await prerenderAll()
    for (const [lang, byPath] of Object.entries(pages)) {
      for (const path of LISTED_PATHS) {
        const page = byPath[path]
        check(`${lang}${path}`, `${page?.title ?? ''}\n${page?.description ?? ''}\n${(page?.body ?? '').replace(/<[^>]+>/g, ' ')}`)
      }
    }
  })

  it('does not say the example classes come in Swedish or Danish', async () => {
    const pages = await prerenderAll()
    for (const lang of ['sv', 'da']) {
      const body = pages[lang]?.['/examples/hill-challenge']?.body ?? ''
      expect(body).toMatch(/English/)
    }
  })
})
