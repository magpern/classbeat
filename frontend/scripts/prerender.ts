import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

// ADR 0030 section 1: renders every public page with react-dom/server into build/prerender.json,
// outside dist so it is never served as a file. `npm run prerender` builds the Vite SSR bundle
// first; any throw, missing <h1>, inline style, fetch or untranslated published page fails the build.
//
//   node scripts/prerender.ts [ssr-bundle-dir]

const root = join(import.meta.dirname, '..')
const bundleDir = process.argv[2] ?? join(root, 'build', 'ssr')
const entry = pathToFileURL(join(bundleDir, 'prerenderAll.js')).href

const { prerenderAll } = (await import(entry)) as { prerenderAll: () => Promise<unknown> }
const pages = await prerenderAll()
mkdirSync(join(root, 'build'), { recursive: true })
writeFileSync(join(root, 'build', 'prerender.json'), `${JSON.stringify(pages, null, 2)}\n`)
console.log('prerender: wrote build/prerender.json')
