import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

// The worker's model is the FakeProvider replaying a recorded script from
// backend/tests/agent_scripts. compose.e2e.yaml reads it through the symlink /fake-run/script.json
// (it starts on good_first_draft); a test points the link at the script the next run needs. Only
// call it while no run is going. Every run reads the file anew, so no restart is needed.

export type ModelScript =
  | 'good_first_draft'
  | 'revise_good'
  | 'revise_second'
  | 'revise_description'
  | 'running_first_draft' // a running design: five zones, an incline per interval (ADR 0018)
  | 'running_revise' // its revision
  | 'soundless_first_draft' // a design with no music source (ADR 0007 amendment of 2026-11-03)
  | 'soundless_revise_good' // its revision: the first minutes easier
  | 'soundless_revise_second' // a second revision: the second step easier
  | 'e2e_pick' // song picking, phase 1: search, pool, choose (the fake Spotify's catalogue)
  | 'e2e_change' // Change songs, phase 1: replace slot 2
  | 'e2e_swedebeat_mix' // design on a cached Swedebeat mix map (ADR 0016 A6)
  | 'e2e_swedebeat_replace' // the catalogue replace of two songs (ADR 0016 B5)
  | 'e2e_interpret' // the interpret stage's submit_brief (ADR 0029 section 4)

export function useModelScript(name: ModelScript): void {
  execFileSync(
    'docker',
    ['compose', 'exec', '-T', 'worker', 'sh', '-c', `ln -sfn /fake-scripts/${name}.json /fake-run/script.json`],
    { cwd: new URL('../..', import.meta.url), stdio: ['ignore', 'ignore', 'ignore'] },
  )
}

interface DraftJson {
  name: string
  description: string
  segments: { name: string; intervals: { sec: number; zone: number }[] }[]
}

/** The draft a script submits, as the fake MOWL shows a saved program of it: its name, segment
 *  names and every interval's zone (the durations are fitted to the music, so they are not
 *  compared). */
export function savedShape(script: ModelScript): {
  name: string
  description: string
  segments: string[]
  zones: number[]
} {
  const file = new URL(`../../backend/tests/agent_scripts/${script}.json`, import.meta.url)
  const drafts = (JSON.parse(readFileSync(file, 'utf8')) as { drafts: Record<string, DraftJson> }).drafts
  const draft = Object.values(drafts)[0]!
  return {
    name: draft.name,
    description: draft.description,
    segments: draft.segments.map((s) => s.name),
    zones: draft.segments.flatMap((s) => s.intervals.map((i) => i.zone)),
  }
}
