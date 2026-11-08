import { useEffect, useState } from 'react'
import { checkHealth } from '../api/health'
import { useT } from '../i18n/I18n'
import type { CatalogueKey } from '../i18n/en'

type ApiState = 'checking' | 'ok' | 'unreachable'

const LABELS: Record<ApiState, CatalogueKey> = {
  checking: 'shell.api.checking',
  ok: 'shell.api.ok',
  unreachable: 'shell.api.unreachable',
}

export function ApiStatus() {
  const t = useT()
  const [state, setState] = useState<ApiState>('checking')

  useEffect(() => {
    const controller = new AbortController()
    void checkHealth(controller.signal).then((healthy) => {
      if (!controller.signal.aborted) setState(healthy ? 'ok' : 'unreachable')
    })
    return () => {
      controller.abort()
    }
  }, [])

  return (
    <p className="api-status" data-state={state} role="status">
      <span className="api-status__dot" aria-hidden="true" />
      {t(LABELS[state])}
    </p>
  )
}
