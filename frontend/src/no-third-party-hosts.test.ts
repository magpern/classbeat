import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import indexHtml from '../index.html?raw'

// Every shipped stylesheet and script is bundled from src/; fonts are bundled too.
// This guards the "no runtime requests to third-party hosts" rule at the source level.
// Vitest returns an empty string for ?raw CSS imports, so the stylesheets are read from disk.
function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    return entry.isDirectory() ? cssFiles(path) : path.endsWith('.css') ? [path] : []
  })
}
const stylesheets: Record<string, string> = Object.fromEntries(
  cssFiles(import.meta.dirname).map((path) => [path, readFileSync(path, 'utf8')]),
)
const scripts = import.meta.glob<string>(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

const REMOTE_URL = /(?:https?:)?\/\/(?!localhost\b|127\.0\.0\.1\b)[a-z0-9.-]+\.[a-z]{2,}/gi

// The one exception (ADR 0013 section 9): album covers from Spotify's image host, named only in
// CoverArt, which shows them. The exemption is that exact quoted string in that one file.
const COVER_ART = './components/songs/CoverArt.tsx'
const COVER_HOST = "'https://i.scdn.co'"

/** The remote URLs in a source file, after the SVG namespace and the one allowed host. */
function remoteUrls(path: string, source: string): string[] {
  // The SVG namespace is an identifier, not a request.
  let checked = source.replaceAll('http://www.w3.org/2000/svg', '')
  if (path === COVER_ART) checked = checked.replaceAll(COVER_HOST, "''")
  return checked.match(REMOTE_URL) ?? []
}

describe('no third-party hosts', () => {
  it('finds the sources it is meant to scan', () => {
    expect(Object.keys(stylesheets).length).toBeGreaterThanOrEqual(2)
    for (const css of Object.values(stylesheets)) expect(css.length).toBeGreaterThan(0)
    expect(Object.keys(scripts).length).toBeGreaterThanOrEqual(3)
    expect(Object.keys(scripts)).toContain(COVER_ART)
  })

  it('index.html loads nothing from a remote host', () => {
    expect(indexHtml.match(REMOTE_URL)).toBeNull()
  })

  it.each(Object.entries({ ...stylesheets, ...scripts }))('%s has no remote URL', (path, source) => {
    expect(remoteUrls(path, source)).toEqual([])
  })

  it('CoverArt names the cover host once and no other host', () => {
    const source = scripts[COVER_ART] ?? ''
    expect(source.split(COVER_HOST).length - 1).toBe(1)
    expect(remoteUrls('./elsewhere.tsx', source)).toEqual(['https://i.scdn.co'])
  })

  it('still fails for the cover host in any other file, and for any other host in CoverArt', () => {
    expect(remoteUrls('./components/songs/SongRows.tsx', `const a = ${COVER_HOST}`)).toEqual([
      'https://i.scdn.co',
    ])
    expect(remoteUrls('./components/songs/CoverArt2.tsx', `const a = ${COVER_HOST}`)).toHaveLength(1)
    for (const other of ['https://mosaic.scdn.co', 'https://evil.example', '//cdn.example.com']) {
      expect(remoteUrls(COVER_ART, `const a = '${other}'`)).toEqual([other])
    }
    // The host inside a longer string is not the exempt string: it is caught too.
    expect(remoteUrls(COVER_ART, "const a = 'https://i.scdn.co/image/x'")).toHaveLength(1)
    expect(remoteUrls(COVER_ART, 'const a = "https://i.scdn.co"')).toHaveLength(1)
  })

  it('no app source names or builds a Spotify URL (links come only from the API)', () => {
    const naming = Object.entries(scripts)
      .filter(([path]) => !path.startsWith('./test/'))
      .filter(([, source]) => /open\.spotify\.com|spotify:(?:track|playlist|album|artist)|spotify\.link/i.test(source))
      .map(([path]) => path)
    expect(naming).toEqual([])
  })

  it('only CoverArt renders an image element', () => {
    // src/test/ holds test fixtures (hostile text such as an <img> in a description), not app code.
    const withImg = Object.entries(scripts)
      .filter(([path]) => !path.startsWith('./test/'))
      .filter(([, source]) => /<img\b|createElement\(\s*['"]img['"]|new Image\(/.test(source))
      .map(([path]) => path)
    expect(withImg).toEqual([COVER_ART])
  })
})
