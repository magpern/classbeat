import { useEffect, useState } from 'react'
import { checkHealth } from '../api/health'

type ApiState = 'checking' | 'ok' | 'unreachable'

const LABELS: Record<ApiState, string> = {
  checking: 'API: checking…',
  ok: 'API: ok',
  unreachable: 'API: unreachable',
}

export function ApiStatus() {
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
      {LABELS[state]}
    </p>
  )
}
