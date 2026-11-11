import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// ADR 0030 section 7 (PP8, #897): every published page goes live fully translated. The prerender
// records the catalogue keys each page rendered (`build/prerender.json`); a key on
// `src/i18n/untranslated.json` renders English in sv and da, so none may appear on a published page.
// On demand, not a CI step, so English-only feature PRs stay possible; the Reviewer re-runs it.
//
//   npm run prerender && npm run check:published-translations

type Prerendered = Record<string, Record<string, { keys: string[] }>>

export interface TranslationReport {
  /** `<lang><path>` of each published page that renders a key still on the untranslated list. */
  problems: string[]
  /** Pages checked: published paths times the languages in prerender.json. */
  checked: number
}

/** The untranslated keys each published page renders, and any page the prerender did not record. */
export function checkPublishedTranslations(
  prerendered: Prerendered,
  published: readonly string[],
  untranslated: readonly string[],
): TranslationReport {
  const problems: string[] = []
  let checked = 0
  const languages = Object.keys(prerendered).filter((lang) => lang !== 'en')
  if (languages.length === 0) problems.push('prerender.json holds no language besides English')
  for (const lang of languages) {
    for (const path of published) {
      const where = `${lang}${path === '' ? '/' : path}`
      const page = prerendered[lang]?.[path]
      if (page === undefined) {
        problems.push(`${where}: not in prerender.json`)
        continue
      }
      checked += 1
      for (const key of page.keys.filter((k) => untranslated.includes(k))) problems.push(`${where}: ${key} is on untranslated.json`)
    }
  }
  return { problems, checked }
}

if (process.argv[1] !== undefined && import.meta.filename === process.argv[1]) {
  const root = join(import.meta.dirname, '..')
  const file = join(root, 'build', 'prerender.json')
  if (!existsSync(file)) {
    console.error('published-translations: build/prerender.json is missing; run `npm run prerender` first')
    process.exit(1)
  }
  const read = (...parts: string[]) => JSON.parse(readFileSync(join(root, ...parts), 'utf8')) as unknown
  const paths = read('src', 'publicSite', 'publicPaths.json') as { published: string[] }
  const untranslated = read('src', 'i18n', 'untranslated.json') as string[]
  const report = checkPublishedTranslations(read('build', 'prerender.json') as Prerendered, paths.published, untranslated)
  for (const problem of report.problems) console.error(problem)
  if (report.problems.length > 0) {
    console.error(`published-translations: ${report.problems.length} problem(s); translate the keys first`)
    process.exit(1)
  }
  console.log(`published-translations: ${report.checked} published pages, none render an untranslated key`)
}
