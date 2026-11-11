import type { DraftInterval, DraftSegment } from '../api/draftTypes'
import type { CatalogueKey } from '../i18n/en'
import data from './examples.json'

// The synthetic example classes of the public pages (ADR 0030, PP5). One versioned file,
// `examples.json`, is the single source of every number on an example page: the chart, the table,
// the length and the library card are all worked out from it, and a test compares the numbers in
// the prompts and prose with it. The backend test reads the same file and runs each class through
// the library's `workout.validate` and the soft-rule engine.
//
// Everything is invented: no song, artist or track is named and no BPM is claimed. The class text
// (part names and cues) stays English in every language, as generated classes do (ADR 0023
// section 7); the words around it are catalogue keys.

/** The only slugs that name an example page. A slug outside this list is a build error and a 404. */
export const EXAMPLE_SLUGS = ['hill-challenge', '90s-dance-intervals', 'endurance-ride', 'running-intervals'] as const
export type ExampleSlug = (typeof EXAMPLE_SLUGS)[number]

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const KINDS: readonly string[] = ['warmup', 'main', 'recovery', 'cooldown']
const VERSION = 1

export type ExampleSport = 'cycling' | 'running'

/** The brief the class was designed from: the fields of the backend `Brief` that apply. */
export interface ExampleBrief {
  length_min: number
  intensity: 'easy' | 'moderate' | 'hard'
  climbs?: number
  sprints?: number
  races?: number
  class_type?: string
}

export interface Example {
  slug: ExampleSlug
  sport: ExampleSport
  brief: ExampleBrief
  segments: DraftSegment[]
}

type Json = Record<string, unknown>

function fail(where: string, what: string): never {
  throw new Error(`examples.json: ${where}: ${what}`)
}

function obj(value: unknown, where: string): Json {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) fail(where, 'not an object')
  return value as Json
}

function whole(value: unknown, where: string, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max) {
    fail(where, `must be a whole number from ${String(min)} to ${String(max)}`)
  }
  return value
}

function interval(raw: unknown, sport: ExampleSport, where: string): DraftInterval {
  const item = obj(raw, where)
  const sec = whole(item.sec, `${where}.sec`, 1, 3600)
  const cue = typeof item.cue === 'string' ? item.cue : fail(where, 'cue must be text')
  if (sport === 'running') {
    const zone = whole(item.zone, `${where}.zone`, 1, 5)
    const gradient = whole(item.gradient, `${where}.gradient`, 0, 1)
    if ('rpm' in item || 'standing' in item || 'game' in item) fail(where, 'a running interval has no rpm, position or game')
    return { sec, zone, rpm: null, standing: false, gradient, cue }
  }
  const zone = whole(item.zone, `${where}.zone`, 1, 7)
  const rpm = item.rpm
  if (!Array.isArray(rpm) || rpm.length !== 2) fail(where, 'rpm must be [from, to]')
  const low = whole(rpm[0], `${where}.rpm[0]`, 0, 200)
  const high = whole(rpm[1], `${where}.rpm[1]`, low, 200)
  if (typeof item.standing !== 'boolean') fail(where, 'standing must be true or false')
  if ('gradient' in item || 'game' in item) fail(where, 'a cycling example has no gradient and no game')
  return { sec, zone, rpm: [low, high], standing: item.standing, cue }
}

function example(raw: unknown, index: number): Example {
  const where = `examples[${String(index)}]`
  const item = obj(raw, where)
  const slug = item.slug
  if (typeof slug !== 'string' || !SLUG.test(slug)) fail(where, 'slug must be lowercase words joined by hyphens')
  if (!(EXAMPLE_SLUGS as readonly string[]).includes(slug)) fail(where, `slug "${slug}" is not a known example`)
  const sport = item.sport
  if (sport !== 'cycling' && sport !== 'running') fail(where, 'sport must be cycling or running')
  const brief = obj(item.brief, `${where}.brief`) as unknown as ExampleBrief
  const minutes = whole(brief.length_min, `${where}.brief.length_min`, 10, 180)
  if (!Array.isArray(item.segments) || item.segments.length === 0) fail(where, 'needs segments')
  const segments = item.segments.map((entry: unknown, s): DraftSegment => {
    const at = `${where}.segments[${String(s)}]`
    const segment = obj(entry, at)
    if (typeof segment.name !== 'string' || segment.name.trim() === '') fail(at, 'needs a name')
    if (typeof segment.kind !== 'string' || !KINDS.includes(segment.kind)) fail(at, `kind must be one of ${KINDS.join(', ')}`)
    if (!Array.isArray(segment.intervals) || segment.intervals.length === 0) fail(at, 'needs intervals')
    return {
      name: segment.name,
      kind: segment.kind,
      intervals: segment.intervals.map((entry2: unknown, i) => interval(entry2, sport, `${at}.intervals[${String(i)}]`)),
    }
  })
  const total = segments.reduce((sum, s) => sum + s.intervals.reduce((n, i) => n + i.sec, 0), 0)
  if (total !== minutes * 60) fail(where, `the segments add up to ${String(total)} s, not the brief's ${String(minutes)} minutes`)
  if (segments.at(-1)?.kind !== 'cooldown') fail(where, 'the last segment must be the cool-down')
  return { slug: slug as ExampleSlug, sport, brief, segments }
}

/** Reads the document and refuses anything off-schema, so a bad edit fails the build, not a page. */
export function parseExamples(raw: unknown): Example[] {
  const document = obj(raw, 'document')
  if (document.version !== VERSION) fail('version', `expected ${String(VERSION)}`)
  if (!Array.isArray(document.examples)) fail('examples', 'not a list')
  const found = document.examples.map(example)
  const slugs = found.map((e) => e.slug)
  if (slugs.join() !== EXAMPLE_SLUGS.join()) fail('examples', `must be, in order: ${EXAMPLE_SLUGS.join(', ')}`)
  return found
}

export const EXAMPLES: readonly Example[] = parseExamples(data)

export function exampleBySlug(slug: string): Example | undefined {
  return EXAMPLES.find((e) => e.slug === slug)
}

export function exampleSeconds(e: Example): number {
  return e.segments.reduce((sum, s) => sum + s.intervals.reduce((n, i) => n + i.sec, 0), 0)
}

/** The class as (zone, seconds) pairs, which is what a library card's `MiniProfile` draws. */
export function exampleProfile(e: Example): [number, number][] {
  return e.segments.flatMap((s) => s.intervals.map((i): [number, number] => [i.zone, i.sec]))
}

export type ExampleField = 'title' | 'summary' | 'audience' | 'prompt' | 'goal1' | 'goal2' | 'goal3'
export const EXAMPLE_FIELDS: readonly ExampleField[] = ['title', 'summary', 'audience', 'prompt', 'goal1', 'goal2', 'goal3']
export const GOAL_FIELDS = ['goal1', 'goal2', 'goal3'] as const

/** The catalogue key of one piece of an example's copy. */
export const exampleKey = (slug: ExampleSlug, field: ExampleField) => `examples.${slug}.${field}` as CatalogueKey

/** The `<title>` and description keys of an example's page (ADR 0030 section 3). */
export function exampleMetaKeys(slug: ExampleSlug): { title: CatalogueKey; description: CatalogueKey } {
  return { title: `meta.example.${slug}.title` as CatalogueKey, description: `meta.example.${slug}.description` as CatalogueKey }
}
