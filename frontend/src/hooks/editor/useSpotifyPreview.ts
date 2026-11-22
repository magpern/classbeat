import { useCallback, useEffect, useRef, useState } from 'react'
import { getSpotifyStatus, type SpotifyStatus } from '../../api/spotify'
import {
  auditionPiece,
  clearCorrection,
  pausePlayer,
  readAlignment,
  readPlayerState,
  setCorrection,
  type Alignment,
  type PreviewFailure,
} from '../../api/spotifyPreview'
import { modelOf } from '../../preview/spotifyAlignmentModel'
import { SpotifyConnectTransport, type SpotifyPlayerApi, type SpotifyView } from '../../preview/spotifyConnectTransport'
import { pollDelayMs } from '../../preview/spotifyObservation'
import type { SpotifyModel } from '../../preview/timeModel'

// The Spotify listening preview's state (ADR 0042 section 7). The transport owns the cursor; this hook
// only schedules its reads: about every second while a play is starting or playing, never while the
// page is hidden, and not before a 429's Retry-After. Nothing here starts a play: `play` is called by
// a click. A correction is one write at a time, and the next one waits for the answer.

export type PreviewLoad =
  | { status: 'loading' }
  | { status: 'failed' }
  /** Not a Spotify draft (or nothing to listen to): the page shows nothing extra. */
  | { status: 'none' }
  | { status: 'ready'; alignment: Alignment; model: SpotifyModel; previewGranted: boolean }

export interface SpotifyPreview {
  load: PreviewLoad
  /** The transport's view now; null until the preview is ready. */
  view: SpotifyView | null
  play: (piece: number, sourceMs: number, classMs: number) => void
  pause: () => void
  /** Sets song `song`'s correction (1-based) to `offsetMs`, or clears it for `null`. */
  correct: (song: number, offsetMs: number | null) => void
  saving: boolean
  /** The last correction write's failure, or null. */
  correctionFailure: PreviewFailure | null
  retry: () => void
}

const TICK_MS = 250

function apiFor(classId: string): SpotifyPlayerApi {
  return {
    audition: (piece, positionMs) => auditionPiece(classId, piece, positionMs),
    pause: () => pausePlayer(classId),
    state: () => readPlayerState(classId),
  }
}

/** `undefined`: the status could not be read. Then the backend decides at Play, and says so in words if it is not granted. */
const grantedOf = (status: SpotifyStatus | null | undefined): boolean => (status === undefined ? true : (status?.previewGranted ?? false))

const visible = (): boolean => document.visibilityState === 'visible'

export function useSpotifyPreview(classId: string): SpotifyPreview {
  const [load, setLoad] = useState<PreviewLoad>({ status: 'loading' })
  const [view, setView] = useState<SpotifyView | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [correctionFailure, setCorrectionFailure] = useState<PreviewFailure | null>(null)
  const transport = useRef<SpotifyConnectTransport | null>(null)
  const alignment = useRef<Alignment | null>(null)
  const writing = useRef(false)
  const kick = useRef<() => void>(() => undefined)
  // The view is read from the transport when something may have changed (never during render).
  const bump = useCallback(() => setView(transport.current?.view() ?? null), [])

  useEffect(() => {
    const abort = new AbortController()
    Promise.all([readAlignment(classId, abort.signal), getSpotifyStatus(abort.signal).catch(() => undefined)])
      .then(([answer, status]) => {
        if (abort.signal.aborted) return
        if (!answer.ok) {
          // A 404 or 409: not a Spotify draft the page can listen to. Anything else: say it could not load.
          setLoad(answer.failure.status === 404 || answer.failure.status === 409 ? { status: 'none' } : { status: 'failed' })
          return
        }
        const model = modelOf(answer.value)
        alignment.current = answer.value
        if (transport.current === null) {
          transport.current = new SpotifyConnectTransport({ api: apiFor(classId), model, now: Date.now })
        } else {
          transport.current.setModel(model)
        }
        setLoad({ status: 'ready', alignment: answer.value, model, previewGranted: grantedOf(status) })
        setView(transport.current.view())
      })
      .catch(() => {
        if (!abort.signal.aborted) setLoad({ status: 'failed' })
      })
    return () => abort.abort()
  }, [classId, attempt])

  const ready = load.status === 'ready'
  useEffect(() => {
    const t = transport.current
    if (!ready || t === null) return
    let timer: ReturnType<typeof setTimeout> | undefined
    let stopped = false
    const stop = () => {
      if (timer !== undefined) clearTimeout(timer)
      timer = undefined
    }
    const schedule = () => {
      stop()
      if (stopped) return
      const delay = pollDelayMs(t.wantsPolling(), visible(), Date.now(), t.notBefore())
      if (delay === null) return
      timer = setTimeout(() => {
        void t.poll().then(() => {
          if (stopped) return
          bump()
          schedule()
        })
      }, delay)
    }
    const onVisibility = () => {
      stop()
      // A hidden page does not poll; when it is shown again, read once at once, then carry on.
      if (visible() && t.wantsPolling()) void t.poll().then(() => !stopped && (bump(), schedule()))
    }
    kick.current = schedule
    document.addEventListener('visibilitychange', onVisibility)
    // The cursor moves between polls, so the page redraws a few times a second while it is shown.
    const ticker = setInterval(() => {
      if (!visible()) return
      const next = t.view()
      // Redraw while anything moves, and once more when it stops (a grace that ran out, a note that expired).
      setView((was) => (was !== null && was.state.status === 'idle' && next.state.status === 'idle' && was.note === next.note ? was : next))
    }, TICK_MS)
    schedule()
    return () => {
      stopped = true
      stop()
      clearInterval(ticker)
      document.removeEventListener('visibilitychange', onVisibility)
      kick.current = () => undefined
    }
  }, [ready, bump])

  const reload = useCallback(() => {
    setLoad({ status: 'loading' })
    setAttempt((n) => n + 1)
  }, [])

  const play = useCallback(
    (piece: number, sourceMs: number, classMs: number) => {
      const t = transport.current
      if (t === null) return
      void t.audition({ piece, sourceMs, classMs }).then(() => {
        bump()
        kick.current()
      })
    },
    [bump],
  )

  const pause = useCallback(() => {
    void transport.current?.pause().then(bump)
  }, [bump])

  const correct = useCallback(
    (song: number, offsetMs: number | null) => {
      const held = alignment.current
      if (held === null || writing.current) return // one write at a time, on the draft that was read
      writing.current = true
      setSaving(true)
      setCorrectionFailure(null)
      // Async, so a call that throws is a rejection the `catch` below turns into a sentence: `writing` never sticks.
      const write = async () => (offsetMs === null ? clearCorrection(classId, song, held.revisionId) : setCorrection(classId, song, held.revisionId, offsetMs))
      void write()
        .then((answer) => {
          if (!answer.ok) {
            setCorrectionFailure(answer.failure)
            // A stale draft: read it again so the next write names the right one.
            if (answer.failure.reason === 'stale_base') reload()
            return
          }
          const model = modelOf(answer.value)
          alignment.current = answer.value
          transport.current?.setModel(model)
          setLoad((was) => (was.status === 'ready' ? { ...was, alignment: answer.value, model } : was))
        })
        .catch(() => setCorrectionFailure({ reason: 'unknown', status: 0, retryAfterS: null }))
        .finally(() => {
          writing.current = false
          setSaving(false)
        })
    },
    [classId, reload],
  )

  return { load, view: ready ? view : null, play, pause, correct, saving, correctionFailure, retry: reload }
}
