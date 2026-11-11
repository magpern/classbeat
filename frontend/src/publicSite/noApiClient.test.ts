import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { describe, expect, it } from 'vitest'
import { runtimeImports, srcPathOf } from './importScan'
import { LISTED_PATHS } from './publicPaths'

// ADR 0030 section 8: what the build-time prerender renders loads no API client, so it can never
// start a request. The guard reads the files it names and checks what each imports directly.
//
// Named exemptions, not scanned: the shell (components/shell), `SupportLink` and `useSupportAddress`
// (components/SupportLink.tsx). They call the API only from effects, which do not run in the
// prerender, so Help, Privacy and Terms render without a support address and the client fills it in
// after takeover.

const srcDir = join(import.meta.dirname, '..')

/** Modules under src/api that hold only types and pure helpers. */
const PURE_API = ['api/draftTypes']

/** The page module of each listed path. A new path must name its module here, or the test fails. */
const PAGE_MODULES: Readonly<Record<string, readonly string[]>> = {
  '': ['pages/LandingPage.tsx'],
  '/privacy': ['pages/PrivacyPage.tsx'],
  '/terms': ['pages/TermsPage.tsx'],
  '/help': ['pages/HelpPage.tsx'],
  '/how-it-works': ['pages/HowItWorksPage.tsx'],
  '/features': ['pages/FeaturesPage.tsx'],
}

/** Content components of the pages, and the chart components they draw. */
const CONTENT_DIRS = ['publicSite', 'components/landing', 'components/publicPages', 'pages/legal']
const CHART_FILES = [
  'components/classes/MiniProfile.tsx',
  'components/classes/ProfileChart.tsx',
  'components/classes/SongStrip.tsx',
  'components/classes/ZoneTime.tsx',
  'components/classes/zoneClasses.ts',
]

function sourcesIn(dir: string): string[] {
  return readdirSync(join(srcDir, dir), { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sourcesIn(path)
    return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name) ? [path.split(sep).join('/')] : []
  })
}

/** The API client modules a file imports directly at run time. */
function apiClientImports(file: string, source: string): string[] {
  return runtimeImports(source)
    .map((specifier) => srcPathOf(file, specifier))
    .filter((path): path is string => path !== null && path.startsWith('api/'))
    .filter((path) => !PURE_API.includes(path.replace(/\.(ts|tsx)$/, '')))
}

const guarded = [
  ...new Set([...CONTENT_DIRS.flatMap(sourcesIn), ...CHART_FILES, ...Object.values(PAGE_MODULES).flat()]),
]

describe('the prerendered pages load no API client', () => {
  it('names a page module for every listed path', () => {
    expect(Object.keys(PAGE_MODULES).sort()).toEqual([...LISTED_PATHS].sort())
  })

  it('guards the files that exist', () => {
    expect(guarded.length).toBeGreaterThan(8)
    for (const file of guarded) expect(readFileSync(join(srcDir, file), 'utf8').length).toBeGreaterThan(0)
  })

  it.each(guarded)('%s imports nothing from src/api but pure types', (file) => {
    expect(apiClientImports(file, readFileSync(join(srcDir, file), 'utf8'))).toEqual([])
  })
})

describe('the scan', () => {
  const file = 'components/landing/X.tsx'

  it('flags a value import, a side-effect import, a re-export and a dynamic import from src/api', () => {
    const source = [
      "import { getSession } from '../../api/auth'",
      "import '../../api/client'",
      "export { listClasses } from '../../api/classes'",
      "const later = import('../../api/jobs')",
    ].join('\n')
    expect(apiClientImports(file, source)).toEqual([
      'api/auth',
      'api/client',
      'api/classes',
      'api/jobs',
    ])
  })

  it('flags a multi-line import and an inline type specifier', () => {
    const source = "import {\n  a,\n  b,\n} from '../../api/plan'\nimport { type Song } from '../../api/songs'"
    expect(apiClientImports(file, source)).toEqual(['api/plan', 'api/songs'])
  })

  it('allows type-only imports, the pure draftTypes, other folders, packages and comments', () => {
    const source = [
      "import type { Song } from '../../api/songs'",
      "export type { Draft } from '../../api/classes'",
      "import { isRunningDraft, type DraftSegment } from '../../api/draftTypes'",
      "import { useT } from '../../i18n/I18n'",
      "import { Flame } from 'lucide-react'",
      "// import { getSession } from '../../api/auth'",
      "/* import { logout } from '../../api/auth' */",
    ].join('\n')
    expect(apiClientImports(file, source)).toEqual([])
  })

  it('catches the shell-style import in a guarded place, so only the named files may have it', () => {
    const shell = readFileSync(join(srcDir, 'components/shell/AppShell.tsx'), 'utf8')
    expect(apiClientImports('components/shell/AppShell.tsx', shell)).toEqual(['api/auth'])
    const support = readFileSync(join(srcDir, 'components/SupportLink.tsx'), 'utf8')
    expect(apiClientImports('components/SupportLink.tsx', support)).toEqual(['api/plan'])
  })
})

describe('react-dom/server', () => {
  it('is imported only by the prerender, never by code the client bundle loads', () => {
    const users = sourcesIn('.')
      .filter((file) => /react-dom\/server/.test(readFileSync(join(srcDir, file), 'utf8')))
      .map((file) => relative('.', file).split(sep).join('/'))
    expect(users).toEqual(['prerender/renderPage.tsx'])
  })
})
