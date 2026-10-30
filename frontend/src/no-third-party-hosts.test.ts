import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import indexHtml from '../index.html?raw'

// Every shipped stylesheet and script is bundled from src/; fonts are system fonts.
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

const REMOTE_URL = /(?:https?:)?\/\/(?!localhost\b|127\.0\.0\.1\b)[a-z0-9.-]+\.[a-z]{2,}/i

describe('no third-party hosts', () => {
  it('finds the sources it is meant to scan', () => {
    expect(Object.keys(stylesheets).length).toBeGreaterThanOrEqual(2)
    for (const css of Object.values(stylesheets)) expect(css.length).toBeGreaterThan(0)
    expect(Object.keys(scripts).length).toBeGreaterThanOrEqual(3)
  })

  it('index.html loads nothing from a remote host', () => {
    expect(indexHtml).not.toMatch(REMOTE_URL)
  })

  it.each(Object.entries({ ...stylesheets, ...scripts }))('%s has no remote URL', (_path, source) => {
    // The SVG namespace is an identifier, not a request.
    expect(source.replaceAll('http://www.w3.org/2000/svg', '')).not.toMatch(REMOTE_URL)
  })
})
