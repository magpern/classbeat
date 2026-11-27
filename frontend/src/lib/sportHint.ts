// The start screen's Cycling/Running toggle follows the request while the instructor types (#1544,
// owner 2026-11-26: "This switch toggles [if] running is mentioned"). Pure: no React, no storage.
//
// - `sportHint(text)`: Running when the text names running (whole words, case aside, in en, sv and
//   da) and names no ride; Cycling otherwise, also when it names both ("a ride, then a run": the
//   reading refuses that politely, `two_sports`) or neither.
// - The toggle state is `{ sport, manual }`. While `manual` is false, `followText` sets the sport
//   from the hint on every change, so it flips back to Cycling when the running words are gone. A
//   choice by hand (`chooseSport`) sets `manual` and always wins: typing never moves it again.
// - `sportOverride(state)`: what the request sends as `sport_override`: only a choice by hand (#1558).
//   A flip by the words stays on the screen and is not sent ("a class we run on Tuesdays" names no
//   run), so the reading decides the sport, and the instructor confirms it. When a sent sport and
//   the words disagree the reading asks (backend `which_sport`).
// - `initialToggle(text, kept)`: a kept request ("Try again") restores its sport; it counts as
//   chosen by hand when it differs from what the words say, so the hint never undoes it.

export type Sport = 'cycling' | 'running'

export interface SportToggle {
  sport: Sport
  /** The instructor chose the sport by hand: the text no longer moves it. */
  manual: boolean
}

// Whole words only, compared case aside. "runners" and "spinal" are other words, so "for
// runners who also ride" names no run, and a word inside a longer one never counts.
const RUNNING_WORDS: ReadonlySet<string> = new Set([
  // en
  'run',
  'runs',
  'running',
  'jog',
  'jogs',
  'jogging',
  'treadmill',
  // sv
  'löpning',
  'löppass',
  'löpa',
  'springa',
  'jogga',
  'joggning',
  'löprunda',
  'löprundor',
  'löpträning',
  'löpband',
  'springpass',
  'intervallöpning',
  // da
  'løb',
  'løbe',
  'løbetur',
  'løbehold',
  'jogge',
  'løbetræning',
  'løbebånd',
  'intervalløb',
])

const CYCLING_WORDS: ReadonlySet<string> = new Set([
  // en
  'ride',
  'rides',
  'riding',
  'cycle',
  'cycling',
  'spin',
  'spinning',
  'bike',
  'biking',
  // sv
  'cykel',
  'cykla',
  'cykling',
  'cykelpass',
  'spinningpass',
  // da
  'cykle',
  'cykelhold',
  'cykeltur',
])

/** The text's words, lower case: runs of letters and digits in any script, so "løb," is "løb". */
export function words(text: string): string[] {
  return text.toLocaleLowerCase().split(/[^\p{L}\p{N}]+/u).filter((word) => word !== '')
}

/** Running when the text names running and no ride; Cycling otherwise. */
export function sportHint(text: string): Sport {
  const said = words(text)
  const runs = said.some((word) => RUNNING_WORDS.has(word))
  const rides = said.some((word) => CYCLING_WORDS.has(word))
  return runs && !rides ? 'running' : 'cycling'
}

export const AUTO_CYCLING: SportToggle = { sport: 'cycling', manual: false }

/** The toggle after the text changed: the hint's sport unless the instructor chose by hand. */
export function followText(state: SportToggle, text: string): SportToggle {
  if (state.manual) return state
  const sport = sportHint(text)
  return sport === state.sport ? state : { sport, manual: false }
}

/** The toggle after the instructor chose a sport by hand: it sticks. */
export function chooseSport(sport: Sport): SportToggle {
  return { sport, manual: true }
}

/** The `sport_override` the request sends (see the module): a choice by hand only, else undefined. */
export function sportOverride(state: SportToggle): Sport | undefined {
  return state.manual ? state.sport : undefined
}

/** The first state: the words' hint, or a kept request's sport (by hand when the words differ). */
export function initialToggle(text: string, kept: Sport | null = null): SportToggle {
  const hinted = sportHint(text)
  if (kept === null || kept === hinted) return { sport: hinted, manual: false }
  return chooseSport(kept)
}
