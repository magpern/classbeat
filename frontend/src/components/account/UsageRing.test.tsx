import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { UsageRing } from './UsageRing'

// Issue 1277: the ring sits on the Plan card, which is `bg-raised`, so a track painted with the
// card's own colour (or the line or surface colours, 1.1 to 1.2:1 against it) is invisible at 0 of N.
// The track is a mix of the muted text colour into the card colour; this checks its contrast from the
// token values, as tokens.test.ts does for the rest of the palette.
const tokensCss = readFileSync(join(import.meta.dirname, '..', '..', 'styles', 'tokens.css'), 'utf8')

function tokens(selector: string): Record<string, string> {
  const start = tokensCss.indexOf(`${selector} {`)
  expect(start, selector).toBeGreaterThanOrEqual(0)
  const body = tokensCss.slice(start, tokensCss.indexOf('}', start))
  const out: Record<string, string> = {}
  for (const m of body.matchAll(/--cb-([a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)) out[m[1] as string] = m[2] as string
  return out
}

const dark = tokens(':root')
const light = { ...dark, ...tokens(":root[data-theme='light']") }

const channel = (hex: string, i: number) => parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16)

function luminance(rgb: number[]): number {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }) as [number, number, number]
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** `color-mix(in srgb, over share%, under)` as the browser computes it, then its contrast against `under`. */
function trackContrast(over: string, under: string, share: number): number {
  const rgb = [0, 1, 2].map((i) => channel(over, i) * share + channel(under, i) * (1 - share))
  const [hi, lo] = [luminance(rgb), luminance([0, 1, 2].map((i) => channel(under, i)))].sort((a, b) => b - a) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

function track(held: number, limit: number): SVGCircleElement {
  render(<UsageRing held={held} limit={limit} />)
  const circle = screen.getByTestId('usage-ring').querySelector('svg > circle')
  expect(circle).not.toBeNull()
  return circle as SVGCircleElement
}

describe('the usage ring track', () => {
  it.each([
    [0, 3],
    [2, 3],
    [3, 3],
  ])('is drawn at %i of %i, in the muted colour mixed into the card colour', (held, limit) => {
    const circle = track(held, limit)
    expect(circle.getAttribute('class')).toContain('[stroke:color-mix(in_srgb,var(--cb-muted)_70%,var(--cb-raised))]')
    // Never the card's own colour, nor the line or surface colour (all about 1.1 to 1.2:1 on the card).
    expect(circle.getAttribute('class')).not.toMatch(/stroke-(raised|line|surface)\b/)
  })

  it.each([
    ['dark', dark],
    ['light', light],
  ])('is at least 3:1 against the card in %s', (_theme, t) => {
    const share = 0.7
    expect(trackContrast(t.muted as string, t.raised as string, share)).toBeGreaterThanOrEqual(3)
    // The three tokens it was rejected for stay too faint, so the test would catch a swap back.
    for (const faint of ['line', 'surface', 'bg']) {
      expect(trackContrast(t[faint] as string, t.raised as string, 1), faint).toBeLessThan(1.3)
    }
  })

  it('draws the arc over the track only when something is used', () => {
    const empty = render(<UsageRing held={0} limit={3} />)
    expect(empty.container.querySelectorAll('svg > circle')).toHaveLength(1)
    empty.unmount()
    const full = render(<UsageRing held={3} limit={3} />)
    expect(full.container.querySelectorAll('svg > circle')).toHaveLength(2)
    expect(screen.getByTestId('usage-ring').getAttribute('aria-label')).toContain('100')
  })
})

describe('the usage ring size', () => {
  it('is compact on a phone and full size from the sm breakpoint, and does not animate', () => {
    render(<UsageRing held={1} limit={3} />)
    const ring = screen.getByTestId('usage-ring')
    expect(ring.className).toContain('size-[5.5rem]')
    expect(ring.className).toContain('sm:size-32')
    expect(ring.className).toContain('shrink-0')
    expect(ring.outerHTML).not.toMatch(/animate|transition|<animate/)
  })
})
