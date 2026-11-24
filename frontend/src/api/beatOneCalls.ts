import { del, request } from './client'
import { decodeBeatOne, type BeatOne } from './beatOneTypes'

// The beat-one calls of a class (ADR 0007 amendment): one module so `classes.ts` stays small.
const base = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

/** Set one song's beat-one correction (-3 to 3, not 0) for the music of `revisionId`. */
export async function setBeatOne(
  classId: string,
  song: number,
  beats: number,
  revisionId: string,
): Promise<BeatOne> {
  const body = await request('PUT', `${base(classId)}/beat-one/${String(song)}`, {
    body: { beats, revision_id: revisionId },
  })
  return decodeBeatOne(body)
}

/** Clear one song's correction; idempotent. */
export async function clearBeatOne(
  classId: string,
  song: number,
  revisionId: string,
): Promise<BeatOne> {
  const query = `?revision_id=${encodeURIComponent(revisionId)}`
  return decodeBeatOne(await del(`${base(classId)}/beat-one/${String(song)}${query}`))
}

/** Clear one correction of the class by its id (for earlier music, or a song not played). */
export async function clearBeatOneRow(classId: string, rowId: string): Promise<BeatOne> {
  return decodeBeatOne(await del(`${base(classId)}/beat-one/rows/${encodeURIComponent(rowId)}`))
}

/** Clear every correction of the class. */
export async function clearAllBeatOne(classId: string): Promise<BeatOne> {
  return decodeBeatOne(await del(`${base(classId)}/beat-one`))
}
