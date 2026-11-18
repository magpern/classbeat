import { useCallback, useEffect, useState } from 'react'
import { listPending, type PendingMessage } from '../../api/pendingMessages'

/**
 * The class's stored chat messages (ADR 0038 section 8). Read when the page mounts, whenever
 * `version` changes (the page reloaded the class) and on `refresh()`, so a reopened tab finds the
 * message that was still pending. A failed read keeps what is shown; `failed` says the last read
 * failed, so a page that waits for the first read can offer another.
 */
export function usePendingMessages(
  classId: string,
  version: unknown,
): { messages: PendingMessage[] | null; failed: boolean; refresh: () => void } {
  const [messages, setMessages] = useState<PendingMessage[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [round, setRound] = useState(0)
  const refresh = useCallback(() => {
    setRound((n) => n + 1)
  }, [])
  useEffect(() => {
    const abort = new AbortController()
    listPending(classId, abort.signal).then(
      (found) => {
        if (abort.signal.aborted) return
        setMessages(found)
        setFailed(false)
      },
      () => {
        if (!abort.signal.aborted) setFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId, version, round])
  return { messages, failed, refresh }
}
