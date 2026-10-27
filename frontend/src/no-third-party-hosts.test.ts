import { describe, expect, it } from 'vitest'
import indexHtml from '../index.html?raw'

// Every shipped stylesheet and script is bundled from src/; fonts are system fonts.
// This guards the "no runtime requests to third-party hosts" rule at the source level.
const stylesheets = import.meta.glob<string>('./**/*.css', {
  query: '?raw',
  import: 'default',
  eager: true,
})
const scripts = import.meta.glob<string>(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

const REMOTE_URL = /(?:https?:)?\/\/(?!localhost\b|127\.0\.0\.1\b)[a-z0-9.-]+\.[a-z]{2,}/i

describe('no third-party hosts', () => {
  it('finds the sources it is meant to scan', () => {
    expect(Object.keys(stylesheets).length).toBeGreaterThanOrEqual(2)
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
