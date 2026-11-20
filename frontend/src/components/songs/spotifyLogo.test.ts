import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { describe, expect, it } from 'vitest'

// The official Spotify files, added unmodified (SHA-256 of each file below). The two Full_Logo files
// (logo with the wordmark) were downloaded 2026-10-31 from
// https://developer.spotify.com/images/guidelines/design/2024-spotify-full-logo.zip. The two
// Primary_Logo files are the standalone icon (the glyph without the wordmark), downloaded 2026-11-19
// from https://developer.spotify.com/images/guidelines/design/2024-spotify-logo-icon.zip. Colours are
// never defined in a component.
const BRAND = join(import.meta.dirname, '..', '..', 'assets', 'brand')
const PINNED: Record<string, string> = {
  'Full_Logo_Black_RGB.svg': 'b91219c14405166e3667deb7f250df4b939a8a5dbc8460ee9525a75672ba9ec5',
  'Full_Logo_White_RGB.svg': '31cdfcdd58d3533a32d287267a1c404f376749b1fc4da99e4baa2233684f053c',
  'Primary_Logo_Black_RGB.svg': '5595afea0e6f009b1dd8529511204d0fd5ca035e49c85409d1697063b3c27a05',
  'Primary_Logo_White_RGB.svg': '8929d148f54cede78f0f36ce90df815e5ea5e5559e7faeccad3669302ef2daa1',
}

describe('the bundled Spotify logo and icon', () => {
  it('is exactly the four official files (two logos, two icons), unmodified', () => {
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

describe('the icon on a connection tile or row', () => {
  // Spotify's guidelines: the icon is never smaller than 21px. `.brand-icon` has a width floor, so the
  // height cannot fall under it, however small the square gets.
  it('has a width floor that keeps the icon at least 22px high', () => {
    const css = readFileSync(join(import.meta.dirname, '..', '..', 'styles', 'app.css'), 'utf8')
    const rule = /\n\.brand-icon \{([^}]*)\}/.exec(css)?.[1] ?? ''
    const floor = /min-width:\s*([\d.]+)rem/.exec(rule)?.[1]
    expect(rule).toMatch(/height:\s*auto/)
    expect(floor).toBeDefined()
    for (const name of ['Primary_Logo_Black_RGB.svg', 'Primary_Logo_White_RGB.svg']) {
      const box = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(readFileSync(join(BRAND, name), 'utf8'))
      const [width, height] = [Number(box?.[1]), Number(box?.[2])]
      expect((Number(floor) * 16 * height) / width, name).toBeGreaterThanOrEqual(22)
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
