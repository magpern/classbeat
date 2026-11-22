import type { Timeline } from '../../api/studioOutputs'

// What the audio of a draft is, as the browser can tell (pure). The preview clips are rendered from the
// stored mix spec and the songs' places in the mix, so when this changes the clips fetched so far no
// longer match the draft. A workout-only edit (a zone, a cue) changes neither, the server's plan
// hash stays and the cached clips stay valid.

export function audioKey(timeline: Timeline): string {
  const places = timeline.tracks.map((t) => `${t.position}:${t.mixStartSec}-${t.mixEndSec}:${t.startSec}`)
  return `${timeline.specDigest}|${places.join(',')}`
}
