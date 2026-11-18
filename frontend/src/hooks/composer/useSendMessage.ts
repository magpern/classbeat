import { useRef, useState } from 'react'
import { storePending } from '../../api/pendingMessages'
import { storeReasonText } from '../../api/pendingReasons'
import { t } from '../../i18n/t'
import { useAction } from '../../components/forms'

export const MAX_MESSAGE_CHARS = 2000

/** Characters the way the backend counts them (code points, not UTF-16 units). */
const length = (value: string): number => Array.from(value).length

/**
 * Sends one chat message of the composer page (ADR 0038 section 8): it is stored as `pending`
 * under a message id made once per text, so a repeated click or a retry after a lost answer stores
 * the same message; the page's driver then submits it when the class has no running job.
 */
export function useSendMessage(classId: string, onStored: () => void) {
  const action = useAction()
  const [problem, setProblem] = useState<string | null>(null)
  const attempt = useRef<{ text: string; id: string } | null>(null)

  const send = async (raw: string): Promise<boolean> => {
    const text = raw.trim()
    let invalid: string | null = null
    if (text === '') invalid = t('class.chat.writeSomething')
    else if (length(text) > MAX_MESSAGE_CHARS) {
      invalid = t('class.chat.tooLong', { max: MAX_MESSAGE_CHARS, count: length(text) })
    }
    setProblem(invalid)
    if (invalid !== null) return false
    if (attempt.current?.text !== text) attempt.current = { text, id: crypto.randomUUID() }
    const id = attempt.current.id
    const stored = await action.run(() => storePending(classId, id, text), storeReasonText)
    if (stored === undefined) return false
    attempt.current = null
    onStored()
    return true
  }
  return { send, busy: action.busy, error: problem ?? action.error }
}
