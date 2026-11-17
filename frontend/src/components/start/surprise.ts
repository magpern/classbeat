import { MAX_REQUEST } from '../../lib/startRequest'

// "Surprise me" (ADR 0038 section 10.1): a curated table of class descriptions, in the languages of the
// catalogues. One is picked at random and started as if the instructor had typed it, so every entry names
// its length in minutes, and only entries within the plan's length are drawn (pure; no network, no model).

export interface Surprise {
  /** The length the description asks for. */
  minutes: number
  text: { en: string; sv: string; da: string }
}

export const SURPRISES: readonly [Surprise, ...Surprise[]] = [
  {
    minutes: 20,
    text: {
      en: 'A 20 minute express ride: a short warm-up, four hard sprints and a quick cool-down.',
      sv: 'Ett expresspass på 20 minuter: kort uppvärmning, fyra hårda sprinter och en snabb nedvarvning.',
      da: 'Et ekspresshold på 20 minutter: kort opvarmning, fire hårde sprints og en hurtig nedkøling.',
    },
  },
  {
    minutes: 30,
    text: {
      en: 'A 30 minute interval ride: six sprints that get shorter and faster, with easy spinning in between.',
      sv: 'Ett intervallpass på 30 minuter: sex sprinter som blir kortare och snabbare, med lugn trampning emellan.',
      da: 'Et intervalhold på 30 minutter: seks sprints, der bliver kortere og hurtigere, med rolig pedalering imellem.',
    },
  },
  {
    minutes: 30,
    text: {
      en: 'A 30 minute lunch break ride: bright and bouncy, two short climbs and a big singalong finish.',
      sv: 'Ett lunchpass på 30 minuter: pigg och studsig, två korta backar och ett stort allsångsavslut.',
      da: 'Et frokosthold på 30 minutter: frisk og livlig, to korte bakker og en stor fællessang til sidst.',
    },
  },
  {
    minutes: 45,
    text: {
      en: 'A 45 minute ride with three climbs and a hard finish, for a Thursday evening crowd.',
      sv: 'Ett pass på 45 minuter med tre backar och ett hårt avslut, för en torsdagskväll.',
      da: 'Et hold på 45 minutter med tre bakker og en hård afslutning, til en torsdag aften.',
    },
  },
  {
    minutes: 45,
    text: {
      en: 'A 45 minute pyramid ride: build up in steps to one long hard effort in the middle, then back down again.',
      sv: 'Ett pyramidpass på 45 minuter: bygg upp i steg till en lång hård insats i mitten och sedan ner igen.',
      da: 'Et pyramidehold på 45 minutter: byg op i trin til én lang hård indsats i midten og så ned igen.',
    },
  },
  {
    minutes: 60,
    text: {
      en: 'A 60 minute easy endurance ride with a steady rhythm, a few gentle hills and a calm finish.',
      sv: 'Ett lugnt distanspass på 60 minuter med jämn rytm, några milda backar och ett lugnt avslut.',
      da: 'Et roligt udholdenhedshold på 60 minutter med jævn rytme, et par milde bakker og en rolig afslutning.',
    },
  },
]

/** The length assumed while the plan is not known: short enough for every plan. */
export const SAFE_MINUTES = 30

export type SurpriseLocale = keyof Surprise['text']

/** The table's language for a locale code (`sv`, `da`; anything else English). */
export function surpriseLocale(locale: string): SurpriseLocale {
  return locale === 'sv' || locale === 'da' ? locale : 'en'
}

/** The entries that fit `maxMinutes` (null: not known yet); the shortest ones when none fits. */
export function fittingSurprises(maxMinutes: number | null): readonly Surprise[] {
  const limit = maxMinutes ?? SAFE_MINUTES
  const fits = SURPRISES.filter((entry) => entry.minutes <= limit)
  if (fits.length > 0) return fits
  const shortest = Math.min(...SURPRISES.map((entry) => entry.minutes))
  return SURPRISES.filter((entry) => entry.minutes === shortest)
}

/**
 * A random description for `locale` within `maxMinutes`. `random` is `Math.random` unless a test gives its own;
 * it returns a number in [0, 1).
 */
export function pickSurprise(locale: string, maxMinutes: number | null, random: () => number = Math.random): string {
  const pool = fittingSurprises(maxMinutes)
  const entry = pool[Math.min(pool.length - 1, Math.floor(random() * pool.length))]
  return (entry ?? SURPRISES[0]).text[surpriseLocale(locale)].slice(0, MAX_REQUEST)
}
