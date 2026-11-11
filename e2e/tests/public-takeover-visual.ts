import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { BrowserContext, Page } from '@playwright/test'

// The takeover gate's two page-level comparisons (docs/plans/public-pages.md, "Takeover gate"):
// the before and after screenshots, compared in the run with the image slots masked, and the check
// that no English text is on an sv or da page. Thresholds are starting values (ADR 0030).

/** Where the screenshots go; CI uploads this directory from every shard, passing or not. */
export const SHOT_DIR = path.resolve(import.meta.dirname, '..', 'takeover-shots')

/** Per-channel difference (0 to 255) under which two pixels count as the same. */
const CHANNEL_TOLERANCE = 16
/** The share of pixels that may differ once the masked parts are painted over. */
export const DIFF_LIMIT = 0.005

/**
 * What is hidden from the comparison because it legitimately differs between the prerendered page
 * and the taken-over one: the image slots (the prerender holds both themes' pictures, the client
 * one) and the header's account area (the prerender writes an invisible placeholder of the same
 * size, the client the language picker and the two links once the session answers).
 */
export function masks(page: Page) {
  return [page.locator('[data-slot]'), page.locator('[data-testid="nav-placeholder"]'), page.locator('header nav[aria-label]')]
}

/** One screenshot of the whole page, masked, saved under SHOT_DIR; the PNG bytes come back. */
export async function shoot(page: Page, name: string): Promise<Buffer> {
  mkdirSync(SHOT_DIR, { recursive: true })
  return page.screenshot({
    path: path.join(SHOT_DIR, `${name}.png`),
    fullPage: true,
    animations: 'disabled',
    caret: 'hide',
    scale: 'css',
    mask: masks(page),
  })
}

export interface Difference {
  ratio: number
  /** Width and height of each image when they differ: the page changed size. */
  sizes: string | null
}

/** Compares two PNGs on a blank scratch page of the same context (no page CSP applies there). */
export async function compare(context: BrowserContext, a: Buffer, b: Buffer): Promise<Difference> {
  const scratch = await context.newPage()
  try {
    return await scratch.evaluate(
      async ({ x, y, tolerance }) => {
        const load = async (b64: string) => {
          const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
          return createImageBitmap(new Blob([bytes], { type: 'image/png' }))
        }
        const [one, two] = await Promise.all([load(x), load(y)])
        if (one.width !== two.width || one.height !== two.height) {
          return { ratio: 1, sizes: `${one.width}x${one.height} then ${two.width}x${two.height}` }
        }
        const pixels = (img: ImageBitmap) => {
          const canvas = new OffscreenCanvas(img.width, img.height)
          const g = canvas.getContext('2d', { willReadFrequently: true })
          if (!g) throw new Error('no 2d canvas')
          g.drawImage(img, 0, 0)
          return g.getImageData(0, 0, img.width, img.height).data
        }
        const p = pixels(one)
        const q = pixels(two)
        let differing = 0
        for (let i = 0; i < p.length; i += 4) {
          const d = Math.max(
            Math.abs((p[i] ?? 0) - (q[i] ?? 0)),
            Math.abs((p[i + 1] ?? 0) - (q[i + 1] ?? 0)),
            Math.abs((p[i + 2] ?? 0) - (q[i + 2] ?? 0)),
          )
          if (d > tolerance) differing++
        }
        return { ratio: differing / (p.length / 4), sizes: null }
      },
      { x: a.toString('base64'), y: b.toString('base64'), tolerance: CHANNEL_TOLERANCE },
    )
  } finally {
    await scratch.close()
  }
}

/** Writes a small note beside the screenshots when a comparison fails, so the artifact says why. */
export function noteDifference(name: string, d: Difference): void {
  mkdirSync(SHOT_DIR, { recursive: true })
  writeFileSync(path.join(SHOT_DIR, `${name}.diff.txt`), `${(d.ratio * 100).toFixed(3)}% of pixels differ${d.sizes ? `; sizes ${d.sizes}` : ''}\n`)
}

// ---- English text on sv and da ----------------------------------------------------------------

/**
 * The text blocks of `#root`: each element's own text and its `aria-label` and `alt`, whitespace
 * collapsed. Drawings are skipped, and so are the part names and cues of an example's table,
 * which are English on purpose (the page says so: `examples.notes.english`). Runs in the browser,
 * on the live page or, with `html`, on the English page's served HTML.
 */
async function blocks(page: Page, html: string | null): Promise<string[]> {
  return page.evaluate((source) => {
    const doc = source === null ? document : new DOMParser().parseFromString(source, 'text/html')
    const root = doc.getElementById('root')
    if (!root) return []
    const out: string[] = []
    const norm = (s: string | null) => (s ?? '').replace(/\s+/g, ' ').trim()
    for (const el of root.querySelectorAll('*')) {
      if (el.closest('svg') || el.closest('[data-testid="example-table"] tbody')) continue
      out.push(
        norm(
          [...el.childNodes]
            .filter((n) => n.nodeType === Node.TEXT_NODE)
            .map((n) => n.textContent)
            .join(' '),
        ),
        norm(el.getAttribute('aria-label')),
        norm(el.getAttribute('alt')),
      )
    }
    return out.filter(Boolean)
  }, html)
}

/** A block long enough to be a sentence or a label of several words, not a brand or a number. */
const isProse = (s: string) => s.length >= 15 && s.split(' ').length >= 3

const english = new Map<string, Promise<Set<string>>>()

/** The English page's prose blocks, fetched once per path and worker (a request, not a navigation). */
function englishProse(context: BrowserContext, scratch: Page, pagePath: string): Promise<Set<string>> {
  let found = english.get(pagePath)
  if (!found) {
    found = context.request
      .get(`/en${pagePath}`)
      .then((r) => r.text())
      .then(async (html) => new Set((await blocks(scratch, html)).filter(isProse)))
    english.set(pagePath, found)
  }
  return found
}

/**
 * The prose on the current page that is word for word the English page's. `scratch` is a blank
 * page used only to parse the English HTML.
 */
export async function englishLeaks(context: BrowserContext, scratch: Page, page: Page, pagePath: string): Promise<string[]> {
  const known = await englishProse(context, scratch, pagePath)
  return (await blocks(page, null)).filter((b) => isProse(b) && known.has(b))
}

const headings = new Map<string, Promise<string>>()

/** The English page's `<h1>`, fetched once per path and worker. */
export function englishHeading(context: BrowserContext, scratch: Page, pagePath: string): Promise<string> {
  let found = headings.get(pagePath)
  if (!found) {
    found = context.request
      .get(`/en${pagePath}`)
      .then((r) => r.text())
      .then((html) =>
        scratch.evaluate(
          (source) => new DOMParser().parseFromString(source, 'text/html').querySelector('#root h1')?.textContent?.trim() ?? '',
          html,
        ),
      )
    headings.set(pagePath, found)
  }
  return found
}
