import type { Page } from '@playwright/test'

// What the takeover gate watches in the browser (docs/plans/public-pages.md, "Takeover gate"):
// the observer that is installed before any page script, and the readings taken before and after
// the client takes the page over.

export interface Seen {
  emptyRoot: number
  loadingCard: number
  h1Values: string[]
  shifts: number
  shiftSources: string[]
  violations: string[]
}

/** Runs before any page script: records what the person could see go wrong. */
export function observe(): void {
  const seen: Seen = { emptyRoot: 0, loadingCard: 0, h1Values: [], shifts: 0, shiftSources: [], violations: [] }
  ;(window as unknown as { __seen: Seen }).__seen = seen
  document.addEventListener('securitypolicyviolation', (e) => {
    seen.violations.push(`${e.violatedDirective} ${e.blockedURI}`)
  })
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as unknown as {
        value: number
        hadRecentInput: boolean
        sources?: { node: Element | null }[]
      }[]) {
        if (entry.hadRecentInput) continue
        seen.shifts += entry.value
        // Name what moved, so a failure says where to look.
        const names = (entry.sources ?? []).map((s) => {
          const n = s.node
          return n ? `${n.tagName.toLowerCase()}${n.id ? `#${n.id}` : ''}.${String(n.getAttribute?.('class') ?? '').slice(0, 40)}` : '?'
        })
        seen.shiftSources.push(`${entry.value.toFixed(4)} ${names.join(' | ')}`)
      }
    }).observe({ type: 'layout-shift', buffered: true })
  } catch {
    // layout-shift is Chromium only; the spec runs there
  }
  let painted = false
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      painted = true
    })
  })
  const look = () => {
    const root = document.getElementById('root')
    if (!root) return
    if (painted && root.firstElementChild === null) seen.emptyRoot++
    // The loading card is a paragraph under a card title (`common.loading`), never the whole root's
    // text, so look for any element whose own text is just that word. It counts at once, painted or not.
    const loading = [...root.querySelectorAll('*')].some(
      (el) =>
        [...el.childNodes]
          .filter((n) => n.nodeType === Node.TEXT_NODE)
          .map((n) => n.textContent ?? '')
          .join('')
          .trim() === 'Loading',
    )
    if (loading) seen.loadingCard++
    const h1 = root.querySelector('h1')?.textContent?.trim()
    if (h1 && seen.h1Values.at(-1) !== h1) seen.h1Values.push(h1)
  }
  new MutationObserver(look).observe(document, { childList: true, subtree: true, characterData: true })
  document.addEventListener('DOMContentLoaded', look)
}

export async function seen(page: Page): Promise<Seen> {
  return page.evaluate(() => (window as unknown as { __seen: Seen }).__seen)
}

/** The boxes that must not move: the heading and, where there is one, the chart. */
export async function boxes(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element | null) => {
      const r = el?.getBoundingClientRect()
      return r ? { x: r.x, y: r.y + window.scrollY, width: r.width, height: r.height } : null
    }
    return { h1: box(document.querySelector('#root h1')), chart: box(document.querySelector('#root [data-testid="profile"]')) }
  })
}

/** The colour the first stop of each zone gradient resolves to: the bars' zone colours. */
export async function zoneColours(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll('#root svg linearGradient')].map((g) => {
      const stop = g.querySelector('stop')
      return stop ? getComputedStyle(stop).stopColor : ''
    }),
  )
}
