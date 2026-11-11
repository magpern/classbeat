import { LISTED } from './public-paths'

// The takeover gate's matrix (docs/plans/public-pages.md, "Takeover gate"; ADR 0030 section 7).

/** Every language the gate covers. */
export const ALL_LANGUAGES = ['en', 'sv', 'da'] as const
export type GateLanguage = (typeof ALL_LANGUAGES)[number]

/**
 * The languages whose cells run: all of them since the sv/da translation of PP4 to PP6 (#896) is on
 * `main`. Narrowing it skips the other languages' cells with a reason; nothing else in the gate changes.
 */
export const GATE_LANGUAGES: readonly GateLanguage[] = ALL_LANGUAGES

export const THEMES = ['dark', 'light'] as const
export const SIZES = [
  { name: '390', width: 390, height: 844 },
  { name: '1440', width: 1440, height: 900 },
] as const

/**
 * A stored theme is read by theme-init.js; a first visit follows the default (dark), whatever the
 * system prefers. So a first visit exists for dark only.
 */
export const VISITS = [
  { theme: 'dark', stored: true },
  { theme: 'light', stored: true },
  { theme: 'dark', stored: false },
] as const

/** Landing, How it works and one example with a chart (those that are listed). */
export const PAGES = ['', '/how-it-works', '/examples/hill-challenge'].filter((p) => LISTED.includes(p))

export const pageName = (p: string) => (p === '' ? 'landing' : p.slice(1).replaceAll('/', '-'))
