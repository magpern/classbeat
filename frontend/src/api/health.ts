export const HEALTH_URL = '/api/health'
export const HEALTH_TIMEOUT_MS = 5000

/**
 * GET /api/health. True only for a 2xx answer whose JSON body says `{"status": "ok"}`;
 * a network error, a timeout, a non-2xx status or any other body counts as unreachable.
 * `signal` cancels the request (for example on unmount).
 */
export async function checkHealth(signal: AbortSignal): Promise<boolean> {
  const request = new AbortController()
  const abort = () => {
    request.abort()
  }
  signal.addEventListener('abort', abort, { once: true })
  const timer = setTimeout(abort, HEALTH_TIMEOUT_MS)
  try {
    const response = await fetch(HEALTH_URL, { signal: request.signal })
    if (!response.ok) return false
    const body: unknown = await response.json()
    return typeof body === 'object' && body !== null && 'status' in body && body.status === 'ok'
  } catch {
    return false
  } finally {
    clearTimeout(timer)
    signal.removeEventListener('abort', abort)
  }
}
