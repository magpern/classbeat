import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { ROUTE_PATHS } from '../App'
import manifest from '../i18n/locales.json'

// ADR 0027 section 8: every in-app link and navigation goes through `Link` and `navigate`, which add
// the language prefix. Nothing else may write an address, and nothing may hard-code a prefix.

const srcDir = join(import.meta.dirname, '..')

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    return entry.isDirectory() ? walk(path) : [path]
  })
}

const sources: Record<string, string> = {}
for (const path of walk(srcDir)) {
  const name = relative(srcDir, path).split(sep).join('/')
  // `test/` holds helpers that open the app at a URL, like a browser; they are not app code.
  if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) && !name.startsWith('test/')) sources[name] = readFileSync(path, 'utf8')
}

// The files that own the browser's address: the router, and the one full-page navigation to a provider.
const ADDRESS_OWNERS = ['router/Router.tsx', 'lib/navigation.ts']
// Targets that are never prefixed (ADR 0027 section 1).
const UNPREFIXED = String.raw`(?:api|oauth|admin)(?:[/?#"'\x60]|$)`
const CODES = manifest.join('|')

/** A raw anchor or link-like attribute whose literal target is an app path. */
const RAW_HREF = new RegExp(String.raw`\bhref=(?:\{\s*)?["'\x60]/(?!${UNPREFIXED})`)
const HISTORY_WRITE = /\bhistory\s*\.\s*(?:pushState|replaceState|go|back|forward)\s*\(/
const LOCATION_WRITE =
  /\b(?:window|document|self|globalThis)?\.?location\s*(?:\.\s*(?:href|pathname|search|hash)\s*=(?!=)|\s*=(?!=)|\.\s*(?:assign|replace)\s*\()/
const HARD_CODED_PREFIX = new RegExp(String.raw`["'\x60]/(?:${CODES})(?:[/?#"'\x60])`)

function addressProblems(name: string, source: string): string[] {
  const found: string[] = []
  const lines = source.split('\n')
  lines.forEach((line, index) => {
    const at = `${name}:${String(index + 1)}`
    // A comment may name a prefix or an address call.
    if (/^\s*(?:\*|\/\*|\/\/)/.test(line)) return
    const code = line.replace(/\s\/\/\s.*$/, '')
    if (RAW_HREF.test(code)) found.push(`${at} raw <a href="/..."> (use Link)`)
    if (HARD_CODED_PREFIX.test(code)) found.push(`${at} hard-coded language prefix`)
    if (ADDRESS_OWNERS.includes(name)) return
    if (HISTORY_WRITE.test(code)) found.push(`${at} direct history call (use navigate)`)
    if (LOCATION_WRITE.test(code)) found.push(`${at} location assignment (use navigate)`)
  })
  return found
}

describe('the link guard', () => {
  it('finds the sources it is meant to scan', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(100)
    expect(sources['router/Router.tsx']).toBeDefined()
    expect(sources['lib/navigation.ts']).toBeDefined()
  })

  it('has no raw link, address write or hard-coded prefix in the app', () => {
    const problems = Object.entries(sources).flatMap(([name, source]) => addressProblems(name, source))
    expect(problems).toEqual([])
  })

  it('keeps the address owners to the two files listed', () => {
    const writers = Object.entries(sources)
      .filter(([, source]) => HISTORY_WRITE.test(source) || LOCATION_WRITE.test(source))
      .map(([name]) => name)
      .sort()
    expect(writers).toEqual([...ADDRESS_OWNERS].sort())
  })

  describe('catches what it is meant to', () => {
    const found = (code: string, name = 'pages/X.tsx') => addressProblems(name, code)

    it('raw anchors to an app path', () => {
      expect(found('<a href="/classes">x</a>')).toHaveLength(1)
      expect(found("<a href='/account'>x</a>")).toHaveLength(1)
      expect(found('<a href={`/jobs/${id}`}>x</a>')).toHaveLength(1)
      expect(found("<a href={'/help'}>x</a>")).toHaveLength(1)
    })

    it('allows the unprefixed areas, external links and mailto', () => {
      expect(found('<a href="/api/mail/unsubscribe?token=x">x</a>')).toEqual([])
      expect(found('<a href="/oauth/google/callback">x</a>')).toEqual([])
      expect(found('<a href="/admin/users">x</a>')).toEqual([])
      expect(found('<a href="https://example.test/">x</a>')).toEqual([])
      expect(found('<a href={`mailto:${address}`}>x</a>')).toEqual([])
      expect(found('<a href={song.openSpotify}>x</a>')).toEqual([])
    })

    it('direct history writes and location assignments outside the owners', () => {
      expect(found("window.history.pushState(null, '', '/x')")).toHaveLength(1)
      expect(found("history.replaceState(null, '', url)")).toHaveLength(1)
      expect(found("window.location.href = '/x'")).toHaveLength(1)
      expect(found("window.location = '/x'")).toHaveLength(1)
      expect(found('window.location.assign(url)')).toHaveLength(1)
      expect(found('window.location.replace(url)')).toHaveLength(1)
      expect(found('const p = window.location.pathname')).toEqual([])
      expect(found('if (window.location.hash === "") return')).toEqual([])
      expect(found("window.history.replaceState(null, '', x)", 'router/Router.tsx')).toEqual([])
      expect(found('window.location.assign(parsed.href)', 'lib/navigation.ts')).toEqual([])
    })

    it('a hard-coded prefix in a link or navigation', () => {
      expect(found("<Link to='/en/privacy'>x</Link>")).toHaveLength(1)
      expect(found('navigate(`/sv/classes/${id}`)')).toHaveLength(1)
      expect(found("navigate('/da')")).toHaveLength(1)
      expect(found('<Link to="/svenska">x</Link>')).toEqual([])
      expect(found("const x = '/endpoint'")).toEqual([])
    })
  })
})

describe('the route table', () => {
  it('has no two-letter first segment, which is the shape of a language code (ADR 0027 section 5)', () => {
    expect(ROUTE_PATHS.length).toBeGreaterThan(15)
    const twoLetter = ROUTE_PATHS.filter((path) => /^\/[A-Za-z]{2}(\/|$)/.test(path))
    expect(twoLetter).toEqual([])
  })

  it('lists the routes the app answers, mail-link pages and the legal pages included', () => {
    for (const path of ['/', '/reset', '/unsubscribe', '/email-change', '/signup/confirm', '/privacy', '/terms', '/classes/new', '/account/spotify']) {
      expect(ROUTE_PATHS).toContain(path)
    }
  })
})
