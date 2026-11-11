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
/** The share of pixels that may differ (0.05%; the en pairs measured 0.000% with the old 0.5% limit). */
export const DIFF_LIMIT = 0.0005

/**
 * What is left out of the comparison because it legitimately differs between the prerendered page
 * and the taken-over one: the pictures in the image slots (the prerender holds both themes'
 * pictures, the client one) and the header's account area (the prerender writes an invisible
 * placeholder of the same size, the client the language picker and the two links once the session
 * answers). Text drawn over a slot, such as the Landing hero's heading, lead and button, is compared.
 */
function accountArea(page: Page) {
  return [page.locator('[data-testid="nav-placeholder"]'), page.locator('header nav:has(a[href$="/signin"])')]
}

/** Hides every slot picture through the CSSOM (the CSP allows it) and returns the way back. */
async function hidePictures(page: Page): Promise<void> {
  await page.evaluate(() => {
    for (const img of document.querySelectorAll<HTMLImageElement>('[data-slot] img')) {
      img.dataset.cbWas = img.style.visibility
      img.style.visibility = 'hidden'
    }
  })
}

async function showPictures(page: Page): Promise<void> {
  await page.evaluate(() => {
    for (const img of document.querySelectorAll<HTMLImageElement>('[data-slot] img')) {
      img.style.visibility = img.dataset.cbWas ?? ''
      delete img.dataset.cbWas
    }
  })
}

/** One screenshot of the whole page, pictures hidden and the account area masked, saved under SHOT_DIR. */
export async function shoot(page: Page, name: string): Promise<Buffer> {
  mkdirSync(SHOT_DIR, { recursive: true })
  await hidePictures(page)
  try {
    return await page.screenshot({
      path: path.join(SHOT_DIR, `${name}.png`),
      fullPage: true,
      animations: 'disabled',
      caret: 'hide',
      scale: 'css',
      mask: accountArea(page),
    })
  } finally {
    await showPictures(page)
  }
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
 * collapsed. The contents of drawings are skipped (an svg's own label is read), and so are the part names and cues of an example's table,
 * which are English on purpose (the page says so: `examples.notes.english`), and the title, artist, BPM and length of Landing's sample
 * songs (listed in the list's `data-fixture-text`; its translated labels are still read). Runs in the browser,
 * on the live page or, with `html`, on the English page's served HTML.
 */
// Fixture data that is English on purpose: an example's part names and cues.
const FIXTURE_DATA = '[data-testid="example-table"] tbody'

async function blocks(page: Page, html: string | null): Promise<string[]> {
  return page.evaluate(({ source, fixture }) => {
    const doc = source === null ? document : new DOMParser().parseFromString(source, 'text/html')
    const root = doc.getElementById('root')
    if (!root) return []
    const out: string[] = []
    const norm = (s: string | null) => (s ?? '').replace(/\s+/g, ' ').trim()
    for (const el of root.querySelectorAll('*')) {
      if (el.parentElement?.closest('svg') || el.closest(fixture)) continue
      // Fixture words inside translated text (a sample song's title and artist) are cut out, the rest is read.
      const own = (el.closest('[data-fixture-text]')?.getAttribute('data-fixture-text') ?? '').split('|').filter(Boolean)
      out.push(
        norm(
          own.reduce(
            (text, word) => text.split(word).join(' '),
            [...el.childNodes]
              .filter((n) => n.nodeType === Node.TEXT_NODE)
              .map((n) => n.textContent)
              .join(' '),
          ),
        ),
        norm(el.getAttribute('aria-label')),
        norm(el.getAttribute('alt')),
      )
    }
    return out.filter(Boolean)
  }, { source: html, fixture: FIXTURE_DATA })
}

/** A block of at least three words and ten characters: a label or a sentence, not a brand or a number. */
const isProse = (s: string) => s.length >= 10 && s.split(' ').length >= 3

const english = new Map<string, Promise<Set<string>>>()

/** The English page's prose blocks, fetched once per path and worker (a request, not a navigation). */
function englishProse(context: BrowserContext, scratch: Page, pagePath: string): Promise<Set<string>> {
  let found = english.get(pagePath)
  if (!found) {
    found = context.request
      .get(`/en${pagePath}`)
      .then((r) => r.text())
      .then(async (html) => new Set((await blocks(scratch, html)).filter(isProse)))
    // A failed fetch must not stay cached for the rest of the worker.
    found.catch(() => english.delete(pagePath))
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
    found.catch(() => headings.delete(pagePath))
    headings.set(pagePath, found)
  }
  return found
}
