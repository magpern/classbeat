import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { BUDGET_BYTES, checkBundle, firstLoadScripts } from '../scripts/bundle-size.ts'

// The first-load JavaScript budget (ADR 0013 section 11): 250 kB gzip. The check runs on the Vite
// build in the `frontend` job (`npm run check:bundle`); here its rules run on small fixtures.

const HTML = `<!doctype html><html><head>
<script src="/theme-init.js"></script>
<script type="module" crossorigin src="/assets/index-abc.js"></script>
<link rel="modulepreload" crossorigin href="/assets/vendor-def.js">
<link rel="stylesheet" crossorigin href="/assets/index-abc.css">
<link rel="icon" href="/favicon.svg">
<script>window.inline = 1</script>
</head><body></body></html>`

const dirs: string[] = []
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

/** A fake dist: index.html, and for each asset that many bytes of noise (it does not compress). */
function dist(sizes: Record<string, number>, html = HTML): string {
  const dir = mkdtempSync(join(tmpdir(), 'cb-dist-'))
  dirs.push(dir)
  mkdirSync(join(dir, 'assets'))
  writeFileSync(join(dir, 'index.html'), html)
  for (const [name, size] of Object.entries(sizes)) writeFileSync(join(dir, name), randomBytes(size))
  return dir
}

describe('first-load scripts', () => {
  it('are the scripts and module preloads of index.html, not styles, icons or inline code', () => {
    expect(firstLoadScripts(HTML)).toEqual(['theme-init.js', 'assets/index-abc.js', 'assets/vendor-def.js'])
  })

  it('refuses a script from another host', () => {
    expect(() => firstLoadScripts('<script src="https://cdn.example/x.js"></script>')).toThrow(/remote script/)
    expect(() => firstLoadScripts('<script src="//cdn.example/x.js"></script>')).toThrow(/remote script/)
  })
})

describe('the budget', () => {
  it('is 250 kB gzip', () => {
    expect(BUDGET_BYTES).toBe(250_000)
  })

  it('passes below it and counts every first-load file', () => {
    const report = checkBundle(dist({ 'theme-init.js': 500, 'assets/index-abc.js': 100_000, 'assets/vendor-def.js': 50_000 }))
    expect(report.ok).toBe(true)
    expect(report.files.map((f) => f.path)).toEqual(['theme-init.js', 'assets/index-abc.js', 'assets/vendor-def.js'])
    expect(report.totalBytes).toBeGreaterThan(150_000)
  })

  it('fails above it, also when it is the preload that tips it over', () => {
    const over = { 'theme-init.js': 500, 'assets/index-abc.js': 160_000, 'assets/vendor-def.js': 100_000 }
    const report = checkBundle(dist(over))
    expect(report.ok).toBe(false)
    expect(report.totalBytes).toBeGreaterThan(BUDGET_BYTES)
    expect(checkBundle(dist({ ...over, 'assets/vendor-def.js': 10_000 })).ok).toBe(true)
  })

  it('does not count a lazy chunk the page does not load first', () => {
    const report = checkBundle(dist({ 'theme-init.js': 1, 'assets/index-abc.js': 1000, 'assets/vendor-def.js': 1000, 'assets/AdminPage-x.js': 900_000 }))
    expect(report.ok).toBe(true)
  })

  it('fails closed when index.html names no bundled script', () => {
    expect(() => checkBundle(dist({}, '<html><body></body></html>'))).toThrow(/no bundled script/)
  })
})
