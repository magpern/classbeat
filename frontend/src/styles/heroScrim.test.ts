import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Issue 854: the hero's title and lead sit on a photo behind a scrim of the page colour. The scrim
// is --cb-bg at the strength --cb-hero-scrim; whatever the photo shows under it, the text must
// keep WCAG AA contrast (4.5:1). The worst photo is the one furthest from the page colour, so the
// check blends the scrim over pure white and pure black and takes the lower contrast.
const dir = import.meta.dirname
const tokensCss = readFileSync(join(dir, 'tokens.css'), 'utf8')
const imageryCss = readFileSync(join(dir, 'imagery.css'), 'utf8')

type Rgb = [number, number, number]

function blockAfter(selector: string): string {
  const start = tokensCss.indexOf(selector + ' {')
  expect(start, selector).toBeGreaterThanOrEqual(0)
  return tokensCss.slice(start, tokensCss.indexOf('}', start))
}

function read(block: string, name: string): string {
  const value = new RegExp(`--cb-${name}:\\s*([^;]+);`).exec(block)?.[1]
  expect(value, name).toBeDefined()
  return (value as string).trim()
}

const rgb = (hex: string): Rgb => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb

function luminance([r, g, b]: Rgb): number {
  const lin = (v: number) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

/** The scrim colour over a photo pixel: alpha of the page colour, the rest the photo. */
const over = (bg: Rgb, alpha: number, photo: Rgb): Rgb => bg.map((c, i) => c * alpha + (photo[i] ?? 0) * (1 - alpha)) as Rgb

// Dark is the base block; light and system-light override it (the same layering as tokens.css).
const darkBlock = blockAfter(':root')
const themes = [
  ['dark', darkBlock, darkBlock],
  ['light', blockAfter(":root[data-theme='light']"), darkBlock],
  ['system (light device)', blockAfter("  :root[data-theme='system']"), darkBlock],
] as const

describe.each(themes)('the hero scrim in %s', (_name, block, base) => {
  const pick = (name: string) => (new RegExp(`--cb-${name}:`).test(block) ? block : base)
  const bg = rgb(read(pick('bg'), 'bg'))
  const alpha = Number(read(pick('hero-scrim'), 'hero-scrim'))

  it('is strong enough for the title and the lead over any photo', () => {
    for (const colour of ['text', 'muted']) {
      const text = rgb(read(pick(colour), colour))
      for (const photo of [[255, 255, 255], [0, 0, 0]] as Rgb[]) {
        expect(contrast(text, over(bg, alpha, photo)), `${colour} over ${photo.join(',')}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('is a strength between 0 and 1', () => {
    expect(alpha).toBeGreaterThan(0)
    expect(alpha).toBeLessThanOrEqual(1)
  })
})

describe('the hero scrim in the stylesheet', () => {
  const hero = imageryCss.slice(imageryCss.indexOf('.hero {'))

  it('is the page colour at the token strength, with no colour of its own', () => {
    expect(hero).toContain('color-mix(in srgb, var(--cb-bg) calc(var(--cb-hero-scrim) * 100%), transparent)')
  })

  it('stays solid as wide as the text column, wide screens and phones', () => {
    const solid = Number(/var\(--hero-solid\) 0%, var\(--hero-solid\) (\d+)%/.exec(hero)?.[1])
    const column = Number(/max-width: min\([\d.]+rem, (\d+)%\)/.exec(hero)?.[1])
    expect(solid).toBeGreaterThanOrEqual(column)
    // on a phone the text sits at the bottom and the scrim rises from there: it is solid under
    // the text block (the bottom 58% of a box at least 32rem tall is more than the text needs)
    const phone = hero.slice(hero.indexOf('@media'))
    expect(phone).toMatch(/linear-gradient\(to top, var\(--hero-solid\) 0%, var\(--hero-solid\) \d+%/)
    expect(phone).toContain('justify-content: flex-end')
  })
})
