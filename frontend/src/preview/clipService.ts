import { createPreviewSession, PreviewError, probeClip, type Clip } from '../api/previewClips'

// What the rendered-clip transport asks of the server: a session, and a window's clip (waiting
// while it renders). An interface, so the transport's tests fake the server without `fetch`.

export interface ClipService {
  createSession(): Promise<string>
  /**
   * The clip that starts at `startMs`. `onRendering` is called each time the server says the clip is
   * still being rendered. Rejects with `PreviewError`, or with the abort when `signal` fires.
   */
  clip(sessionId: string, startMs: number, signal: AbortSignal, onRendering: () => void): Promise<Clip>
}

/** The longest the browser waits for one clip to render before it says so. */
export const MAX_RENDER_WAIT_MS = 3 * 60 * 1000

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const done = () => {
      signal.removeEventListener('abort', aborted)
      resolve()
    }
    const id = setTimeout(done, ms)
    const aborted = () => {
      clearTimeout(id)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal.addEventListener('abort', aborted, { once: true })
  })
}

export function httpClipService(classId: string): ClipService {
  return {
    createSession: () => createPreviewSession(classId),
    async clip(sessionId, startMs, signal, onRendering) {
      let waited = 0
      for (;;) {
        const answer = await probeClip(classId, sessionId, startMs, signal)
        if (answer.kind === 'ready') return answer.clip
        onRendering()
        waited += answer.retryAfterMs
        if (waited > MAX_RENDER_WAIT_MS) throw new PreviewError('render_failed')
        await sleep(answer.retryAfterMs, signal)
      }
    },
  }
}
