import { useEffect, useState } from 'react'
import type { ClassSongs } from '../../api/classSongs'
import { getStudioList, type Named } from '../../api/studioSongs'

/**
 * The approved songs' names by approved-list position, which the mix choices use (the timeline
 * names its pieces in mix order, not by list position). Empty until read, and when the list cannot
 * be read: the rows then say "Song N".
 */
export function useApprovedSongs(classId: string, songs: ClassSongs): ReadonlyMap<number, Named> {
  const revisionId = songs.state === 'approved' ? songs.revisionId : null
  const [found, setFound] = useState<{ key: string; names: ReadonlyMap<number, Named> } | null>(null)
  const key = `${classId}/${revisionId ?? ''}`
  useEffect(() => {
    if (revisionId === null) return
    const abort = new AbortController()
    getStudioList(classId, revisionId, abort.signal).then(
      (list) => {
        if (abort.signal.aborted) return
        const names = new Map(
          list.songs.map((s) => [s.position, { title: s.swedebeat?.title ?? s.title, artist: s.swedebeat?.artist ?? s.artist }]),
        )
        setFound({ key, names })
      },
      () => {
        // Without names the rows are numbered.
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId, revisionId, key])
  return found?.key === key ? found.names : new Map()
}
