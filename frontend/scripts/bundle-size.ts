import { readFileSync } from 'node:fs'
import { join, normalize, sep } from 'node:path'
import { gzipSync } from 'node:zlib'

// ADR 0013 section 11 (UI10): the first-load JavaScript, from the Vite build's own output, stays
// under 250 kB gzip. First load is every script the built index.html asks for at once: the entry
// module and its static preloads (`<script src>`, `<link rel="modulepreload">`). Lazy chunks (the
// admin area) are not part of it. kB is 1000 bytes, as Vite reports it.
//
//   node scripts/bundle-size.ts [dist-dir]     exits 1 above the budget (CI: `npm run check:bundle`)

export const BUDGET_BYTES = 250 * 1000

/** The script files (paths inside dist) the page loads before it can run. */
export function firstLoadScripts(indexHtml: string): string[] {
  const urls: string[] = []
  for (const tag of indexHtml.match(/<(?:script|link)\b[^>]*>/gi) ?? []) {
    const isScript = /^<script\b/i.test(tag)
    const isPreload = /\brel=["']?modulepreload\b/i.test(tag)
    if (!isScript && !isPreload) continue
    const url = /\b(?:src|href)=["']([^"']+)["']/i.exec(tag)?.[1]
    if (url === undefined) continue // an inline script has no file
    if (/^(?:[a-z][a-z0-9+.-]*:)?\/\//i.test(url)) throw new Error(`a remote script in index.html: ${url}`)
    urls.push(url.replace(/^\//, '').split(/[?#]/)[0] ?? url)
  }
  return [...new Set(urls)]
}

export interface BundleReport {
  files: { path: string; gzipBytes: number }[]
  totalBytes: number
  budgetBytes: number
  ok: boolean
}

/** Gzip size of every first-load script of a built `dist` folder, against the budget. */
export function checkBundle(distDir: string, budgetBytes = BUDGET_BYTES): BundleReport {
  const paths = firstLoadScripts(readFileSync(join(distDir, 'index.html'), 'utf8'))
  if (!paths.some((path) => path.startsWith('assets/'))) {
    throw new Error('no bundled script found in index.html: is this the Vite build output?')
  }
  const files = paths.map((path) => {
    const file = normalize(join(distDir, path))
    if (!file.startsWith(normalize(distDir) + sep)) throw new Error(`a script outside dist: ${path}`)
    return { path, gzipBytes: gzipSync(readFileSync(file), { level: 9 }).length }
  })
  const totalBytes = files.reduce((sum, file) => sum + file.gzipBytes, 0)
  return { files, totalBytes, budgetBytes, ok: totalBytes <= budgetBytes }
}

const kB = (bytes: number) => `${(bytes / 1000).toFixed(1)} kB`

if (process.argv[1] !== undefined && import.meta.filename === process.argv[1]) {
  const report = checkBundle(process.argv[2] ?? join(import.meta.dirname, '..', 'dist'))
  for (const file of report.files) console.log(`${kB(file.gzipBytes).padStart(10)}  ${file.path}`)
  console.log(`${kB(report.totalBytes).padStart(10)}  first-load JavaScript, gzip (budget ${kB(report.budgetBytes)})`)
  if (!report.ok) {
    console.error(`first-load JavaScript is over the budget by ${kB(report.totalBytes - report.budgetBytes)}`)
    process.exit(1)
  }
}
