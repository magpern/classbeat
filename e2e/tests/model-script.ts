import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

// The worker's model is the FakeProvider replaying a recorded script from
// backend/tests/agent_scripts. compose.e2e.yaml reads it through the symlink /fake-run/script.json
// (it starts on good_first_draft); a test points the link at the script the next run needs. Only
// call it while no run is going. Every run reads the file anew, so no restart is needed.

export type ModelScript =
  | 'good_first_draft'
  | 'form_climbs_missed' // the good draft (one climb) submitted until the correction budget is spent
  | 'revise_good'
  | 'revise_second'
  | 'revise_description'
  | 'running_first_draft' // a running design: five zones, an incline per interval (ADR 0018)
  | 'running_revise' // its revision
  | 'soundless_first_draft' // a design with no music source (ADR 0007 amendment of 2026-11-03)
  | 'soundless_revise_good' // its revision: the first minutes easier
  | 'soundless_revise_second' // a second revision: the second step easier
  | 'e2e_pick' // song picking, phase 1: search, pool, choose (the fake Spotify's catalogue)
  | 'e2e_change' // Change songs, phase 1: the interpret read (#1023), then replace slot 2
  | 'e2e_swedebeat_mix' // design on a cached Swedebeat mix map (ADR 0016 A6)
  | 'e2e_swedebeat_replace' // the catalogue replace of two songs (ADR 0016 B5)
  | 'e2e_interpret' // the interpret stage's submit_brief (ADR 0029 section 4)
  | 'ai_first_read_pull' // ai-first.spec.ts: reads "Power Pull after climb 2, 2 minutes"
  | 'ai_first_design_pull' // its design: 30 minutes on the e2e music, a Power Pull after climb 2
  | 'ai_first_revise_overflow' // the revise "change it to Overflow": reads it, then the same class with Overflow
  | 'ai_first_read_any' // reads a generic game after climb 2
  | 'ai_first_design_any' // its design: a Racetrack after climb 2
  | 'ai_first_read_long' // reads a 90 minute class (beyond a 60 minute plan)
  | 'ai_first_design_long' // its design: 60 minutes with no music
  | 'e2e_studio_swap' // a studio pick with swaps: 10 of its 14 candidates missing (SR C8)
  | 'e2e_studio_revise' // its edit loop: a chat revise that cuts the last song at 230 s (SR C8)
  | 'composer_read' // composer-workspace.spec.ts: pins "Signal Harbor 18" on the climb, asks how long
  | 'composer_sketch' // its sketch design: no songs, time-based segments
  | 'composer_sketch_revised' // the sketch after the quick answer "60 minutes"
  | 'composer_sketch_again' // the sketch after "Make the last climb longer."
  | 'composer_fill' // the fill: pick pool, choose, then the design stage on the chosen songs
  | 'composer_swap' // the swap of one song (row 4)
  | 'composer_release_read' // composer-release-*.spec.ts: the issue's request, read as six sections
  | 'composer_release_read_styled' // the same reading with a music style ("rock"), for the re-read after the instructor typed one (#1538)
  | 'composer_release_sketch' // its sketch: 60 minutes, every segment tagged, six efforts in section 3
  | 'composer_release_seven' // "Make it seven bursts": the reading of it, then the sketch with seven
  | 'composer_release_fill' // the fill on 15 songs and the design with seven efforts (no pin, no cues)
  | 'e2e_studio_design' // a studio design and mix on the seeded analyses (SR C8; backend design_script(MIX_8))

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
