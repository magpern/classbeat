// A synthetic example class for the landing page. Everything here is invented: the song titles
// and artists are placeholders, never real recordings (ADR 0013 section 5, like the e2e fake).

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
  part: string
  bpm: number
  length: string
}

export const EXAMPLE_SONGS: readonly ExampleSong[] = [
  { title: 'Sample Song One', artist: 'Example Artist A', part: 'Warm-up', bpm: 96, length: '4:10' },
  { title: 'Sample Song Two', artist: 'Example Artist B', part: 'First climb', bpm: 112, length: '3:45' },
  { title: 'Sample Song Three', artist: 'Example Artist C', part: 'Sprints', bpm: 128, length: '3:20' },
  { title: 'Sample Song Four', artist: 'Example Artist D', part: 'Second climb', bpm: 104, length: '4:30' },
  { title: 'Sample Song Five', artist: 'Example Artist E', part: 'Cool-down', bpm: 88, length: '3:50' },
]

export const STEPS: readonly { title: string; text: string }[] = [
  { title: 'Describe', text: 'Tell classbeat the length, the effort and the playlist you want to use.' },
  { title: 'Approve songs', text: 'Look through the songs it picked and swap the ones that do not fit.' },
  { title: 'Save to MOWL', text: 'Your finished class goes straight to your MOWL account, ready to ride.' },
]
