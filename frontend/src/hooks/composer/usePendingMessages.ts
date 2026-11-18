import { useCallback, useEffect, useState } from 'react'
import { listPending, type PendingMessage } from '../../api/pendingMessages'

/**
 * The class's stored chat messages (ADR 0038 section 8). Read when the page mounts, whenever
 * `version` changes (the page reloaded the class) and on `refresh()`, so a reopened tab finds the
 * message that was still pending. A failed read keeps what is shown.
 */
export function usePendingMessages(
  classId: string,
  version: unknown,
): { messages: PendingMessage[] | null; refresh: () => void } {
  const [messages, setMessages] = useState<PendingMessage[] | null>(null)
  const [round, setRound] = useState(0)
  const refresh = useCallback(() => {
    setRound((n) => n + 1)
  }, [])
  useEffect(() => {
    const abort = new AbortController()
    listPending(classId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setMessages(found)
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [classId, version, round])
  return { messages, refresh }
}
