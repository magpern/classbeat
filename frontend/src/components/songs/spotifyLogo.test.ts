import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { describe, expect, it } from 'vitest'

// The official Spotify logo files, downloaded 2026-10-31 from
// https://developer.spotify.com/images/guidelines/design/2024-spotify-full-logo.zip and added
// unmodified (SHA-256 of each file below). Colours are never defined in a component.
const BRAND = join(import.meta.dirname, '..', '..', 'assets', 'brand')
const PINNED: Record<string, string> = {
  'Full_Logo_Black_RGB.svg': 'b91219c14405166e3667deb7f250df4b939a8a5dbc8460ee9525a75672ba9ec5',
  'Full_Logo_White_RGB.svg': '31cdfcdd58d3533a32d287267a1c404f376749b1fc4da99e4baa2233684f053c',
}

describe('the bundled Spotify logo', () => {
  it('is exactly the two official files, unmodified', () => {
    expect(readdirSync(BRAND).sort()).toEqual(Object.keys(PINNED).sort())
    for (const [name, sha] of Object.entries(PINNED)) {
      expect(createHash('sha256').update(readFileSync(join(BRAND, name))).digest('hex'), name).toBe(sha)
    }
  })

  it('is an image and nothing else: no script, link, embedded image or event handler', () => {
    for (const name of Object.keys(PINNED)) {
      const svg = readFileSync(join(BRAND, name), 'utf8')
      expect(svg, name).toMatch(/^<\?xml[^>]*\?><svg /)
      expect(svg, name).not.toMatch(/<script|<foreignObject|<image|<a[\s>]|\son\w+=|href=|data:|javascript:/i)
    }
  })
})

describe('the logo attribution', () => {
  it('is readable as "Spotify" in either theme: neither image is aria-hidden', async () => {
    const { render } = await import('@testing-library/react')
    const { SpotifyAttribution } = await import('./SpotifyAttribution')
    const { container } = render(createElement(SpotifyAttribution))
    const images = [...container.querySelectorAll('img')]
    expect(images).toHaveLength(2)
    for (const img of images) {
      expect(img.getAttribute('alt')).toBe('Spotify')
      expect(img.hasAttribute('aria-hidden')).toBe(false)
    }
  })

  it('is never inlined as a data: URI by the build', async () => {
    const { inlineLimit } = await import('../../../vite.config')
    for (const name of Object.keys(PINNED)) {
      expect(inlineLimit(join(BRAND, name))).toBe(false)
      expect(inlineLimit(join(BRAND, name).replaceAll('/', String.fromCharCode(92)))).toBe(false)
    }
    expect(inlineLimit('/x/src/assets/other.svg')).toBeUndefined()
  })
})
