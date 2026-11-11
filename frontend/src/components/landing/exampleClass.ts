// A synthetic example class for the landing page. Everything here is invented: the song titles
// and artists are placeholders, never real recordings (ADR 0013 section 5, like the e2e fake), so
// they stay as written in every language; the labels around them are catalogue keys.

import type { CatalogueKey } from '../../i18n/en'
import type { SlotId } from '../imagery/imageSlots'

/** Intervals as [zone, seconds]: a warm-up, two climbs, a sprint set, a cool-down (about 30 min). */
export const EXAMPLE_PROFILE: [number, number][] = [
  [1, 120],
  [2, 180],
  [3, 150],
  [4, 120],
  [3, 60],
  [5, 120],
  [3, 60],
  [5, 120],
  [2, 60],
  [6, 30],
  [2, 30],
  [6, 30],
  [2, 30],
  [7, 20],
  [2, 60],
  [3, 120],
  [4, 180],
  [3, 120],
  [2, 70],
  [1, 120],
]

export interface ExampleSong {
  title: string
  artist: string
  part: CatalogueKey
  bpm: number
  length: string
}

export const EXAMPLE_SONGS: readonly ExampleSong[] = [
  { title: 'Sample Song One', artist: 'Example Artist A', part: 'landing.example.part.warmUp', bpm: 96, length: '4:10' },
  { title: 'Sample Song Two', artist: 'Example Artist B', part: 'landing.example.part.firstClimb', bpm: 112, length: '3:45' },
  { title: 'Sample Song Three', artist: 'Example Artist C', part: 'landing.example.part.sprints', bpm: 128, length: '3:20' },
  { title: 'Sample Song Four', artist: 'Example Artist D', part: 'landing.example.part.secondClimb', bpm: 104, length: '4:30' },
  { title: 'Sample Song Five', artist: 'Example Artist E', part: 'landing.example.part.coolDown', bpm: 88, length: '3:50' },
]

export const STEPS: readonly { title: CatalogueKey; text: CatalogueKey; image: SlotId }[] = [
  { title: 'landing.how.describe.title', text: 'landing.how.describe.text', image: 'stepDescribe' },
  { title: 'landing.how.refine.title', text: 'landing.how.refine.text', image: 'stepApprove' },
  { title: 'landing.how.save.title', text: 'landing.how.save.text', image: 'stepSave' },
]
