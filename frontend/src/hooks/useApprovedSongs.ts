import { useEffect, useState } from 'react'
import type { ClassSummary } from '../api/classes'
import { getSongList, type Song } from '../api/songs'

/** The approved song list, for the strip under the axis; null (hidden) until there is one. */
export function useApprovedSongs(classId: string, summary: ClassSummary): Song[] | null {
  const revisionId = summary.songs.state === 'approved' ? summary.songs.revisionId : null
  const [loaded, setLoaded] = useState<{ id: string; songs: Song[] } | null>(null)
  useEffect(() => {
    if (revisionId === null) return
    const abort = new AbortController()
    getSongList(classId, revisionId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setLoaded({ id: revisionId, songs: found.songs })
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [classId, revisionId])
  return loaded !== null && loaded.id === revisionId ? loaded.songs : null
}
