import { useCallback, useEffect, useRef, useState } from 'react'
import { httpClipService } from '../../preview/clipService'
import { RenderedClipTransport } from '../../preview/renderedClipTransport'
import type { TransportState } from '../../preview/transport'

const POLL_MS = 250
const IDLE: TransportState = {
  status: 'idle',
  observedAt: 0,
  uncertaintyMs: 0,
}

function audioElement(index: number): HTMLAudioElement {
  const el = document.createElement('audio')
  el.preload = 'auto'
  el.hidden = true
  el.dataset.testid = `preview-audio-${index}`
  el.setAttribute('data-testid', `preview-audio-${index}`)
  document.body.appendChild(el)
  return el
}

/**
 * The studio draft's audio preview (ADR 0042 section 6): the rendered-clip transport over two hidden
 * audio elements. The cursor is only what `observe()` said: one read after each change the transport
 * announces and a poll while it plays. `audioKey` changing means the draft's audio changed, so the
 * transport fetches fresh clips (the session and its counter are kept).
 */
export function useRenderedPreview(options: { classId: string; mixEndMs: number; audioKey: string }): {
  state: TransportState
  audition: (piece: number, sourceMs: number, classMs: number) => void
  pause: () => void
} {
  const { classId, mixEndMs, audioKey } = options
  const transport = useRef<RenderedClipTransport | null>(null)
  const teardown = useRef<(() => void) | null>(null)
  const mixEnd = useRef(mixEndMs)
  const [state, setState] = useState<TransportState>(IDLE)
  // The transport exists from the first render's effect on, and also when a press comes first: React runs
  // passive effects after paint, and on a loaded machine a Play press can arrive before they did (the press
  // was dropped then, and the editor test of it failed about one run in ten).
  const ensure = useCallback((): RenderedClipTransport => {
    if (transport.current !== null) return transport.current
    const elements = [audioElement(0), audioElement(1)] as const
    const created = new RenderedClipTransport({ service: httpClipService(classId), audio: elements, now: () => performance.now(), mixEndMs: mixEnd.current })
    const off = created.subscribe(() => {
      setState(created.observe())
    })
    transport.current = created
    teardown.current = () => {
      off()
      transport.current = null
      teardown.current = null
      created.dispose()
      for (const el of elements) el.remove()
    }
    return created
  }, [classId])
  useEffect(() => {
    ensure()
    return () => {
      teardown.current?.()
    }
  }, [ensure])
  useEffect(() => {
    mixEnd.current = mixEndMs
    transport.current?.setMixEnd(mixEndMs)
  }, [mixEndMs])
  // The draft's audio changed: the clips fetched so far no longer match it (the session is kept).
  const seen = useRef(audioKey)
  useEffect(() => {
    if (seen.current === audioKey) return
    seen.current = audioKey
    transport.current?.invalidate()
  }, [audioKey])
  const playing = state.status === 'playing' || state.status === 'starting'
  useEffect(() => {
    if (!playing) return
    const id = window.setInterval(() => {
      if (transport.current !== null) setState(transport.current.observe())
    }, POLL_MS)
    return () => {
      window.clearInterval(id)
    }
  }, [playing])
  const audition = useCallback((piece: number, sourceMs: number, classMs: number) => {
    const current = ensure()
    void current.audition({ piece, sourceMs, classMs }).then(() => {
      setState(current.observe())
    })
  }, [ensure])
  const pause = useCallback(() => {
    const current = transport.current
    if (current === null) return
    void current.pause().then(() => {
      setState(current.observe())
    })
  }, [])
  return { state, audition, pause }
}
