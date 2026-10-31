import { createHmac } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import AxeBuilder from '@axe-core/playwright'
import { type Browser, type BrowserContext, type Locator, type Page } from '@playwright/test'

// Shared by the MU done-when specs (ADR 0013 sections 9 and 10): a signed-in browser context that
// may only reach the stack, the viewport / theme / axe audit of one page, and a TOTP code for the
// admin's second step. Public test values only.

export const VIEWPORTS = [
  { name: 'phone 390', width: 390, height: 844 },
  { name: 'desktop 1440', width: 1440, height: 900 },
] as const
export const THEMES = ['dark', 'light'] as const

/** What the audit of one page found, so the spec can fail with every finding at once. */
export interface Findings {
  page: string
  problems: string[]
}

/**
 * A context with its own client address (separate rate limits) that aborts every origin but the
 * stack's, the way `helpers.ts` does for the default context. `covers` names an origin that a spec
 * answers itself (the cover host, with a route of its own): it is not blocked here.
 */
export async function stackContext(browser: Browser, baseURL: string): Promise<BrowserContext> {
  const octet = () => 1 + Math.floor(Math.random() * 254)
  const context = await browser.newContext({
    baseURL,
    extraHTTPHeaders: { 'X-Forwarded-For': `203.0.${octet()}.${octet()}` },
  })
  const allowed = new URL(baseURL).origin
  await context.route(
    (url) => url.origin !== allowed,
    (route) => route.abort('blockedbyclient'),
  )
  return context
}

/** Console errors and CSP violations of a page, collected from now on. */
export function watchConsole(page: Page): string[] {
  const seen: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error' || /Content Security Policy|Refused to/.test(message.text())) {
      seen.push(`${message.type()}: ${message.text()}`)
    }
  })
  page.on('pageerror', (error) => {
    seen.push(`pageerror: ${error.message}`)
  })
  return seen
}

/** The elements that reach past the right edge of the window, outermost first (for the message). */
async function overflowCulprits(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth
    const out: string[] = []
    for (const el of document.body.querySelectorAll('*')) {
      const box = el.getBoundingClientRect()
      // Inside a box that scrolls on its own (a wide table) the overhang is not the page's.
      const scrolls = (node: Element | null): boolean =>
        node !== null && node !== document.body && (['auto', 'scroll'].includes(getComputedStyle(node).overflowX) || scrolls(node.parentElement))
      if (box.width > 0 && box.right > width + 0.5 && !scrolls(el.parentElement)) {
        out.push(`${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ')[0] : ''} (right ${String(Math.round(box.right))})`)
      }
    }
    return out.slice(0, 3)
  })
}

/** True when the page scrolls sideways: the document is wider than the window. */
export async function overflowPixels(page: Page): Promise<number> {
  return page.evaluate(() => {
    const root = document.documentElement
    return Math.max(root.scrollWidth, document.body.scrollWidth) - root.clientWidth
  })
}

export async function setTheme(page: Page, theme: (typeof THEMES)[number]): Promise<void> {
  await page.evaluate(async (value) => {
    document.documentElement.setAttribute('data-theme', value)
    // Two frames, so the colours are the new theme's before axe reads them (the app's own
    // transitions are off under reduced motion, which `audit` asks for).
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  }, theme)
}

/**
 * The checks every page gets (ADR 0013 section 10), at 390 and 1440 px in the dark and the light
 * theme: no sideways scroll, the primary action visible and inside the window, and no serious or
 * critical axe violation. Returns the findings (empty when the page is fine); the page stays at
 * the last viewport, so the caller sets one before the next page.
 */
export async function audit(page: Page, name: string, primary: Locator): Promise<Findings> {
  const problems: string[] = []
  // Reduced motion turns the colour transitions off (tailwind.css), so axe never reads a colour
  // halfway between the two themes.
  await page.emulateMedia({ reducedMotion: 'reduce' })
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    for (const theme of THEMES) {
      await setTheme(page, theme)
      const where = `${viewport.name}, ${theme}`
      const over = await overflowPixels(page)
      if (over > 0) problems.push(
          `${where}: the page scrolls sideways by ${String(over)} px: ${(await overflowCulprits(page)).join(', ')}`,
        )

      const first = primary.first()
      if (!(await first.isVisible())) {
        problems.push(`${where}: the primary action is not visible`)
      } else {
        await first.scrollIntoViewIfNeeded()
        const box = await first.boundingBox()
        if (box === null || box.x < 0 || box.x + box.width > viewport.width + 0.5) {
          problems.push(`${where}: the primary action is outside the window`)
        }
      }

      const result = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze()
      for (const violation of result.violations) {
        if (violation.impact !== 'serious' && violation.impact !== 'critical') continue
        const targets = violation.nodes.map((node) => `${node.target.join(' ')} ${node.html.slice(0, 160)}`).slice(0, 4)
        problems.push(`${where}: axe ${violation.impact} ${violation.id}: ${targets.join(' | ')}`)
      }
    }
  }
  return { page: name, problems }
}

// --- the admin's TOTP (RFC 6238: SHA-1, 6 digits, 30 s) -------------------------------------

function base32Decode(text: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of text.replace(/=+$/, '').toUpperCase()) {
    const index = alphabet.indexOf(char)
    if (index < 0) throw new Error('not a base32 key')
    bits += index.toString(2).padStart(5, '0')
  }
  const bytes: number[] = []
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2))
  return Buffer.from(bytes)
}

/** The code for the 30-second step `offset` steps from now. */
export function totpCode(secret: string, offset = 0): string {
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000) + offset))
  const digest = createHmac('sha1', base32Decode(secret)).update(counter).digest()
  const at = digest[digest.length - 1]! & 0x0f
  const value = digest.readUInt32BE(at) & 0x7fffffff
  return String(value % 1_000_000).padStart(6, '0')
}

/** Make an existing user an admin with the operator CLI (it revokes the user's sessions). */
export function grantAdmin(email: string): void {
  execFileSync('docker', ['compose', 'exec', '-T', 'web', 'python', '-m', 'classbeat', 'admin', 'grant', email], {
    cwd: new URL('../..', import.meta.url),
    stdio: ['ignore', 'ignore', 'ignore'],
  })
}
