import type { Message } from '../en'

// The pure checks behind the catalogue and manifest guards (ADR 0023 section 8, ML3). Each returns
// a list of problems, empty when the input is fine, so the guard can run them on the real files and
// the tests can run them on broken fixtures. Test-only code: the app never imports it.

export type Entries = Readonly<Record<string, Message | undefined>>

const BARE = /\{[A-Za-z_][A-Za-z0-9_]*\}/g
const MARKUP = /<\/?[A-Za-z][^>]*>|&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]*);/i

/** The strings of a message: itself, or each plural form. */
function forms(message: Message): [string, string][] {
  return typeof message === 'string' ? [['', message]] : Object.entries(message)
}

function placeholders(text: string): Set<string> {
  return new Set([...text.matchAll(BARE)].map((m) => m[0].slice(1, -1)))
}

function union(message: Message): Set<string> {
  return new Set(forms(message).flatMap(([, text]) => [...placeholders(text)]))
}

const same = (a: ReadonlySet<string>, b: ReadonlySet<string>) => a.size === b.size && [...a].every((x) => b.has(x))
const show = (names: ReadonlySet<string>) => (names.size === 0 ? 'none' : `{${[...names].sort().join('}, {')}}`)

/** Placeholders must be bare `{name}`; no markup. Any other brace or tag is a problem. */
export function checkText(key: string, message: Message): string[] {
  const problems: string[] = []
  for (const [form, text] of forms(message)) {
    const where = form === '' ? key : `${key}.${form}`
    if (/[{}]/.test(text.replace(BARE, ''))) problems.push(`${where}: placeholders must be a bare {name}`)
    if (MARKUP.test(text)) problems.push(`${where}: no markup in a string (use Trans slots)`)
  }
  return problems
}

/** A plural message has exactly the categories of `locale`; a plain string is allowed anywhere else. */
export function checkPlural(locale: string, key: string, message: Message): string[] {
  if (typeof message === 'string') return []
  const needed = new Intl.PluralRules(locale).resolvedOptions().pluralCategories
  const have = Object.keys(message)
  const problems: string[] = []
  for (const category of needed) if (!have.includes(category)) problems.push(`${key}: ${locale} needs the plural form "${category}"`)
  for (const category of have)
    if (!(needed as string[]).includes(category)) problems.push(`${key}: "${category}" is not a plural category of ${locale}`)
  return problems
}

/** Checks one catalogue against English and the untranslated list. English is checked against itself. */
export function checkCatalogue(
  locale: string,
  english: Entries,
  catalogue: Entries,
  untranslated: readonly string[],
): string[] {
  const problems: string[] = []
  const isEnglish = catalogue === english

  if (!isEnglish) {
    for (const key of Object.keys(english))
      if (!Object.hasOwn(catalogue, key) && !untranslated.includes(key)) problems.push(`${locale}: ${key} is missing`)
    for (const key of Object.keys(catalogue))
      if (!Object.hasOwn(english, key)) problems.push(`${locale}: ${key} is not an English key`)
    for (const key of untranslated) {
      if (!Object.hasOwn(english, key)) problems.push(`${locale}: untranslated.json lists ${key}, which English does not have`)
      else if (Object.hasOwn(catalogue, key)) problems.push(`${locale}: untranslated.json lists ${key}, which is translated; remove it`)
    }
  }

  for (const [key, message] of Object.entries(catalogue)) {
    if (message === undefined) {
      problems.push(`${locale}: ${key} is undefined`)
      continue
    }
    problems.push(...checkText(`${locale}: ${key}`, message), ...checkPlural(locale, `${locale}: ${key}`, message))
    const source = english[key]
    if (isEnglish || source === undefined) continue
    if (typeof source !== typeof message) {
      problems.push(`${locale}: ${key} is ${typeof message === 'string' ? 'a plain string' : 'plural'} but English is not`)
      continue
    }
    const want = union(source)
    if (typeof message === 'string') {
      if (!same(placeholders(message), want)) problems.push(`${locale}: ${key} has placeholders ${show(placeholders(message))}, English has ${show(want)}`)
      continue
    }
    // Plural forms may leave a name out of a form ("one class"), but never add one or drop it from all.
    if (!same(union(message), want)) problems.push(`${locale}: ${key} has placeholders ${show(union(message))}, English has ${show(want)}`)
    for (const [form, text] of forms(message))
      for (const name of placeholders(text))
        if (!want.has(name)) problems.push(`${locale}: ${key}.${form} uses {${name}}, which English does not`)
  }
  return problems
}

/** The manifest, the import map, the catalogue files and the pre-paint list must name the same locales. */
export function checkManifest(input: {
  manifest: readonly string[]
  loaders: readonly string[]
  files: readonly string[]
  initList: readonly string[]
}): string[] {
  const { manifest, loaders, files, initList } = input
  const problems: string[] = []
  if (manifest[0] !== 'en') problems.push('locales.json must start with "en"')
  if (new Set(manifest).size !== manifest.length) problems.push('locales.json lists a locale twice')
  for (const code of manifest) if (!/^[a-z]{2}$/.test(code)) problems.push(`locales.json: "${code}" is not a two-letter code`)
  const others = manifest.filter((code) => code !== 'en')
  const compare = (name: string, actual: readonly string[], expected: readonly string[]) => {
    for (const code of expected) if (!actual.includes(code)) problems.push(`${name} has no "${code}", which locales.json lists`)
    for (const code of actual) if (!expected.includes(code)) problems.push(`${name} has "${code}", which locales.json does not list`)
  }
  compare('the import map (catalogues.ts)', loaders, others)
  compare('the catalogue files (src/i18n/<code>.ts)', files, others)
  compare('public/locale-init.js', initList, manifest)
  return problems
}
