import { useEffect, useState } from 'react'
import { getInterpretation } from '../../api/interpret'
import type { InterpretationItem } from '../../api/interpretationItems'
import { NO_EXTRAS, type ReadingExtras } from '../../api/readingExtras'

export interface Reading {
  items: InterpretationItem[] | null
  extras: ReadingExtras
  interpretJobId: string | null
}

const NONE: Reading = { items: null, extras: NO_EXTRAS, interpretJobId: null }

/**
 * The stored reading of the request, read whatever state the class is in. `useHandoff` reads it
 * only while the class has no version, so a class whose sketch is current needs this one for the
 * card and its questions. A reading that is not stored (409 `not_ready`) or a failed read is no
 * reading; `version` (the page's current revision) reads it again once the class has moved on.
 */
export function useReading(classId: string, version: string | null): Reading {
  const [reading, setReading] = useState<Reading>(NONE)
  useEffect(() => {
    const abort = new AbortController()
    getInterpretation(classId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) {
          setReading({ items: found.items, extras: found.extras, interpretJobId: found.interpretJobId })
        }
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [classId, version])
  return reading
}
