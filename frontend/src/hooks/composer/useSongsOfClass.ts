import { useEffect, useState } from 'react'
import type { ClassSongs } from '../../api/classSongs'
import { getSongList, type SongList } from '../../api/songs'

/**
 * The class's newest song list while it waits for approval or is approved (ADR 0038 section 5):
 * the songs for the segment list, and for a waiting list its designed class (`preview`) and the
 * ids the approval names. A new list (a swap or a change stores one) is read again by its
 * revision id; a list that is no longer shown reads as none.
 */
export function useSongsOfClass(classId: string, songs: ClassSongs): SongList | null {
  const revisionId = (songs.state === 'waiting' || songs.state === 'approved') && songs.revisionId !== null ? songs.revisionId : null
  const [loaded, setLoaded] = useState<SongList | null>(null)
  useEffect(() => {
    if (revisionId === null) return
    const abort = new AbortController()
    getSongList(classId, revisionId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setLoaded(found)
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [classId, revisionId])
  return revisionId !== null && loaded?.id === revisionId ? loaded : null
}
