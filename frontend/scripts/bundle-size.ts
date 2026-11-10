import { readdirSync, readFileSync } from 'node:fs'
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

// react-dom/server belongs to the build-time prerender only (ADR 0030 section 1). Its export names
// survive minification, so any of them in a shipped script means it leaked into the client bundle.
const SERVER_RENDERER = /renderToString|renderToStaticMarkup|renderToPipeableStream|renderToReadableStream|react-dom\/server/

/** The shipped scripts (paths inside dist) that contain the server renderer. */
export function serverRendererIn(distDir: string): string[] {
  const found: string[] = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(join(distDir, dir), { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) walk(path)
      else if (entry.name.endsWith('.js') && SERVER_RENDERER.test(readFileSync(join(distDir, path), 'utf8'))) {
        found.push(path.split(sep).join('/'))
      }
    }
  }
  walk('.')
  return found
}

export interface BundleReport {
  files: { path: string; gzipBytes: number }[]
  totalBytes: number
  budgetBytes: number
  /** Scripts that contain react-dom/server: there must be none. */
  serverRenderer: string[]
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
  const serverRenderer = serverRendererIn(distDir)
  return { files, totalBytes, budgetBytes, serverRenderer, ok: totalBytes <= budgetBytes && serverRenderer.length === 0 }
}

const kB = (bytes: number) => `${(bytes / 1000).toFixed(1)} kB`

if (process.argv[1] !== undefined && import.meta.filename === process.argv[1]) {
  const report = checkBundle(process.argv[2] ?? join(import.meta.dirname, '..', 'dist'))
  for (const file of report.files) console.log(`${kB(file.gzipBytes).padStart(10)}  ${file.path}`)
  console.log(`${kB(report.totalBytes).padStart(10)}  first-load JavaScript, gzip (budget ${kB(report.budgetBytes)})`)
  if (report.serverRenderer.length > 0) {
    console.error(`react-dom/server is in the client bundle: ${report.serverRenderer.join(', ')}`)
    process.exit(1)
  }
  if (!report.ok) {
    console.error(`first-load JavaScript is over the budget by ${kB(report.totalBytes - report.budgetBytes)}`)
    process.exit(1)
  }
}
