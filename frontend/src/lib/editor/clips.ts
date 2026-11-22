import type { Timeline } from '../../api/studioOutputs'
import type { StudioClip } from '../../preview/timeModel'

// The timeline's pieces as the preview time model's studio clips (class time in ms). Pure.

/** One clip per piece, in play order. The cut's start in the analysed file is the track's `startSec`. */
export function studioClips(timeline: Timeline): StudioClip[] {
  return timeline.tracks.map((track) => ({
    piece: track.position,
    label: track.title,
    startMs: Math.round(track.mixStartSec * 1000),
    endMs: Math.round(track.mixEndSec * 1000),
    fadeInMs: Math.round(track.crossfadeInSec * 1000),
    fadeOutMs: Math.round(track.crossfadeOutSec * 1000),
    sourceStartMs: Math.round(track.startSec * 1000),
  }))
}

/** The piece labels by play-order index. */
export function pieceLabels(timeline: Timeline): string[] {
  return timeline.tracks.map((t) => t.title)
}
