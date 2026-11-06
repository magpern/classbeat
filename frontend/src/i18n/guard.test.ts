import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import allowlist from './hardcoded-allowlist.json'
import untranslated from './untranslated.json'
import manifest from './locales.json'
import initScript from '../../public/locale-init.js?raw'
import { CATALOGUE_LOADERS } from './catalogues'
import { en } from './en'
import type { Catalogue } from './en'
import { checkCatalogue, checkManifest, checkPlural, checkText } from './testing/checks'
import { hasWords, isExempt, scanBareFormatting, scanSource } from './testing/scan'

// ADR 0023 section 8 (ML3): no new hard-coded text, complete and consistent catalogues, one manifest.

const srcDir = join(import.meta.dirname, '..')

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    return entry.isDirectory() ? walk(path) : [path]
  })
}

const sources: Record<string, string> = {}
for (const path of walk(srcDir)) {
  const name = relative(srcDir, path).split(sep).join('/')
  if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) sources[name] = readFileSync(path, 'utf8')
}
const tsx = Object.keys(sources).filter((name) => name.endsWith('.tsx') && !isExempt(name))

describe('hard-coded text in .tsx', () => {
  // The allowlist is files not yet moved to the catalogue (ML5a to ML5d). It only shrinks: a file
  // that is clean again must leave the list, and the ceiling is lowered with it. Raising the ceiling
  // or adding a file is the change this guard exists to stop.
  const CEILING = 26

  it('finds the sources it is meant to scan', () => {
    expect(tsx.length).toBeGreaterThan(80)
    expect(tsx).toContain('pages/HomePage.tsx')
  })

  it('catches JSX text, expression strings and text attributes', () => {
    const found = (code: string) => scanSource(`const a = ${code}`).map((f) => f.text)
    expect(found('<p>Save the class</p>')).toEqual(['Save the class'])
    expect(found('<p>{ok ? "Saved" : "Failed"}</p>')).toEqual(['Saved', 'Failed'])
    expect(found('<p>{`Hello ${name}`}</p>')).toEqual(['Hello'])
    expect(found('<input aria-label="Email" placeholder="you@example.com" />')).toEqual(['Email', 'you@example.com'])
    expect(found('<Field label="Name" hint={"Shown to you"} />')).toEqual(['Name', 'Shown to you'])
    expect(found('<img alt="" title="Close" />')).toEqual(['Close'])
  })

  it('catches props that end in a text word, not props that merely contain one', () => {
    const found = (code: string) => scanSource(`const a = ${code}`).map((f) => f.text)
    expect(found('<Dialog confirmLabel="Delete it" emptyTitle="Nothing here" submitText="Go" />')).toEqual([
      'Delete it',
      'Nothing here',
      'Go',
    ])
    expect(found('<Row labelId="x" titleCase="y" textAlign="center" />')).toEqual([])
  })

  it('counts a brand inside a longer word as text', () => {
    expect(hasWords('Googled')).toBe(true)
    expect(hasWords('Spotify')).toBe(false)
    expect(hasWords('Spotify and Google')).toBe(true)
  })

  it('lets through what is not a sentence', () => {
    const found = (code: string) => scanSource(`const a = ${code}`)
    expect(found('<p>{t("a.b")}</p>')).toEqual([])
    expect(found('<p>{count} / {total}</p>')).toEqual([])
    expect(found('<p>&nbsp;&middot;&nbsp;</p>')).toEqual([])
    expect(found('<p>Spotify</p>')).toEqual([])
    expect(found('<p>MOWL &middot; Swedebeat</p>')).toEqual([])
    expect(found('<p>— 12 —</p>')).toEqual([])
    expect(found('<div className="flex" id="x" data-testid="row" type="button" />')).toEqual([])
    expect(found('<p>{items.map((i) => <li key={i}>{i}</li>)}</p>')).toEqual([])
    expect(hasWords('Connect Spotify')).toBe(true)
  })

  it('reports the line of a literal', () => {
    expect(scanSource('const a = (\n  <p>\n    Hello\n  </p>\n)')).toEqual([{ line: 3, text: 'Hello' }])
  })

  it('has no literal outside the allowlist', () => {
    const listed = new Set(allowlist)
    const bad = tsx
      .filter((name) => !listed.has(name))
      .flatMap((name) => scanSource(sources[name] ?? '', name).map((f) => `${name}:${f.line} "${f.text}"`))
    expect(bad, 'Write a key in src/i18n/en.ts and use t() or Trans; do not add the file to the allowlist').toEqual([])
  })

  it('lists no file twice, no file that is gone or exempt, and none that is clean', () => {
    expect(new Set(allowlist).size).toBe(allowlist.length)
    const stale = allowlist.filter((name) => !tsx.includes(name) || scanSource(sources[name] ?? '', name).length === 0)
    expect(stale, 'Remove these from hardcoded-allowlist.json and lower CEILING').toEqual([])
  })

  it('only shrinks', () => {
    expect(allowlist.length, 'A file must not be added to the allowlist').toBeLessThanOrEqual(CEILING)
    expect(allowlist.length, 'Lower CEILING to the new length').toBe(CEILING)
  })
})

describe('bare number and date formatting', () => {
  it('is caught', () => {
    expect(scanBareFormatting('const a = n.toLocaleString()\nconst b = x\n  .toFixed(2)')).toEqual([1, 2])
    expect(scanBareFormatting('d.toLocaleDateString("sv")')).toEqual([1])
    expect(scanBareFormatting('s.toLocaleUpperCase()')).toEqual([])
    expect(scanBareFormatting('// n.toFixed(2)')).toEqual([])
  })

  it('is used nowhere outside format.ts and the admin area', () => {
    const bad = Object.entries(sources)
      .filter(([name]) => name !== 'i18n/format.ts' && !name.startsWith('pages/admin/') && !name.startsWith('i18n/testing/'))
      .flatMap(([name, source]) => scanBareFormatting(source).map((line) => `${name}:${line}`))
    expect(bad, 'Use the helpers in src/i18n/format.ts').toEqual([])
  })
})

describe('catalogues', () => {
  // Every <code>.ts next to en.ts, as the ML7 catalogues will be (default export, like testing/xx.ts).
  const modules = import.meta.glob<Catalogue>('./[a-z][a-z].ts', { eager: true, import: 'default' })
  const present = Object.entries(modules)
    .map(([path, catalogue]) => [/\.\/([a-z]{2})\.ts$/.exec(path)?.[1] ?? '', catalogue] as const)
    .filter(([code]) => code !== 'en')

  it('lists only keys English has', () => {
    const stale = untranslated.filter((key) => !Object.hasOwn(en, key))
    expect(stale, 'untranslated.json names keys that are gone from en.ts').toEqual([])
    expect(new Set(untranslated).size).toBe(untranslated.length)
  })

  it('English is well formed', () => {
    expect(checkCatalogue('en', en, en, [])).toEqual([])
  })

  it.each(present)('%s is complete and consistent', (code, catalogue) => {
    expect(checkCatalogue(code, en, catalogue, untranslated)).toEqual([])
  })

  describe('the checks', () => {
    const english = {
      'a.title': 'Hello {name}',
      'a.count': { one: '{count} class', other: '{count} classes' },
      'a.plain': 'Plain',
    }
    const check = (catalogue: Record<string, unknown>, list: string[] = []) =>
      checkCatalogue('sv', english, catalogue as never, list)
    const full = { 'a.title': 'Hej {name}', 'a.count': { one: '{count} pass', other: '{count} pass' }, 'a.plain': 'Enkel' }

    it('passes a complete catalogue', () => {
      expect(check(full)).toEqual([])
    })

    it('fails a missing key unless it is listed', () => {
      const rest: Record<string, unknown> = { ...full }
      delete rest['a.plain']
      expect(check(rest)).toEqual(['sv: a.plain is missing'])
      expect(check(rest, ['a.plain'])).toEqual([])
    })

    it('fails a listed key that is translated or unknown', () => {
      expect(check(full, ['a.plain']).join()).toContain('is translated')
      expect(check(full, ['a.gone']).join()).toContain('which English does not have')
    })

    it('fails a key English does not have', () => {
      expect(check({ ...full, 'a.extra': 'x' }).join()).toContain('is not an English key')
    })

    it('fails a renamed, added or dropped placeholder', () => {
      expect(check({ ...full, 'a.title': 'Hej {namn}' }).join()).toContain('placeholders {namn}, English has {name}')
      expect(check({ ...full, 'a.title': 'Hej' }).join()).toContain('English has {name}')
      expect(check({ ...full, 'a.title': 'Hej {name} {other}' }).join()).toContain('{name}, {other}')
      expect(check({ ...full, 'a.count': { one: '{n} pass', other: '{count} pass' } }).join()).toContain('a.count')
    })

    it('lets a plural form leave a name out but not use a new one', () => {
      expect(check({ ...full, 'a.count': { one: 'ett pass', other: '{count} pass' } })).toEqual([])
    })

    it('fails placeholders that are not bare names', () => {
      for (const bad of ['{x.attr}', '{x[0]}', '{name:>99999}', '{name!r}', '{0}', '{}', '{ name }', '{name', 'name}']) {
        expect(checkText('k', `Hej ${bad}`), bad).not.toEqual([])
      }
      expect(checkText('k', 'Hej {name} och {other_1}')).toEqual([])
    })

    it('fails markup', () => {
      for (const bad of ['<b>Hej</b>', 'a <br/> b', 'x &amp; y', 'a &#39; b']) expect(checkText('k', bad), bad).not.toEqual([])
      expect(checkText('k', 'a < b and c > d')).toEqual([])
    })

    it('fails plural forms the locale does not use or needs', () => {
      expect(checkPlural('sv', 'k', { one: 'a', other: 'b' })).toEqual([])
      expect(checkPlural('sv', 'k', { other: 'b' }).join()).toContain('needs the plural form "one"')
      expect(checkPlural('sv', 'k', { one: 'a', few: 'c', other: 'b' }).join()).toContain('"few" is not a plural category')
      expect(checkPlural('pl', 'k', { one: 'a', few: 'c', many: 'd', other: 'b' })).toEqual([])
      expect(checkPlural('sv', 'k', 'plain')).toEqual([])
    })

    it('fails a plain string for a plural or the reverse', () => {
      expect(check({ ...full, 'a.count': '{count} pass' }).join()).toContain('is a plain string but English is not')
      expect(check({ ...full, 'a.plain': { one: 'a', other: 'b' } }).join()).toContain('is plural but English is not')
    })
  })
})

describe('the locale manifest', () => {
  const files = Object.keys(import.meta.glob('./[a-z][a-z].ts'))
    .map((path) => /\.\/([a-z]{2})\.ts$/.exec(path)?.[1] ?? '')
    .filter((code) => code !== 'en')
  const initList = JSON.parse(/var locales = (\[[^\]]*\])/.exec(initScript)?.[1]?.replaceAll("'", '"') ?? 'null') as string[]

  it('equals the import map, the catalogue files and locale-init.js', () => {
    expect(checkManifest({ manifest, loaders: Object.keys(CATALOGUE_LOADERS), files, initList })).toEqual([])
  })

  it('fails a locale without a catalogue, or the reverse', () => {
    const ok = { manifest: ['en', 'sv'], loaders: ['sv'], files: ['sv'], initList: ['en', 'sv'] }
    expect(checkManifest(ok)).toEqual([])
    expect(checkManifest({ ...ok, files: [] }).join()).toContain('catalogue files')
    expect(checkManifest({ ...ok, files: ['sv', 'da'] }).join()).toContain('has "da", which locales.json does not list')
    expect(checkManifest({ ...ok, loaders: [] }).join()).toContain('import map')
    expect(checkManifest({ ...ok, initList: ['en'] }).join()).toContain('locale-init.js has no "sv"')
    expect(checkManifest({ ...ok, manifest: ['sv', 'en'] }).join()).toContain('must start with "en"')
    expect(checkManifest({ ...ok, manifest: ['en', 'sv', 'swe'] }).join()).toContain('two-letter')
  })
})
