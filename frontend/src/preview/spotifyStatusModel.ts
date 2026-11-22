import type { PreviewKey } from '../i18n/preview.en'
import { failureKey, retryWaitS } from './spotifyErrorModel'
import type { SpotifyView } from './spotifyConnectTransport'

// The words for what the Spotify transport reports (ADR 0042 section 7.3). Pure: a view in, a
// catalogue key and its numbers out. Every state and failure has a sentence; none shows a code.

export interface Sentence {
  key: PreviewKey
  params: Record<string, string | number>
  tone: 'info' | 'warn' | 'error'
}

/** The headline of the state: what is happening, or why there is no cursor. `device` is the device's name or the "your Spotify app" fallback. */
export function stateSentence(view: SpotifyView, device: string): Sentence {
  const failure = view.failure
  if (failure !== null) {
    const seconds = retryWaitS(failure)
    return { key: failureKey(failure), params: seconds === null ? {} : { seconds }, tone: 'error' }
  }
  const { status, reason } = view.state
  switch (status) {
    case 'starting':
      return { key: 'preview.state.starting', params: {}, tone: 'info' }
    case 'playing':
      return { key: 'preview.state.playing', params: { device }, tone: 'info' }
    case 'paused':
      return { key: 'preview.state.paused', params: { device }, tone: 'info' }
    case 'external':
      return { key: 'preview.state.external', params: {}, tone: 'warn' }
    case 'unaligned':
      return { key: 'preview.state.unaligned', params: {}, tone: 'warn' }
    case 'outside_window':
      return { key: reason === 'after' ? 'preview.state.outside_after' : 'preview.state.outside_before', params: {}, tone: 'warn' }
    case 'idle':
      return { key: reason === 'ended' ? 'preview.state.ended' : 'preview.state.idle', params: {}, tone: 'info' }
    default:
      // `no_device` and `error` always carry a failure (above); this is the safe fallback.
      return { key: 'preview.err.generic', params: {}, tone: 'error' }
  }
}

/** The extra line for a seek or a device switch, for the few seconds it stays on the view. */
export function noteSentence(view: SpotifyView, device: string): Sentence | null {
  if (view.note === 'seek_detected') return { key: 'preview.state.seek', params: {}, tone: 'info' }
  if (view.note === 'device_switched') return { key: 'preview.state.device', params: { device }, tone: 'info' }
  return null
}

/** The cursor line: its class time and band, labelled as approximate inside an overlap; none without a cursor. */
export function cursorSentence(view: SpotifyView, time: string): Sentence {
  if (view.classMs === null) return { key: 'preview.cursor.none', params: {}, tone: 'info' }
  const ms = view.state.uncertaintyMs
  return view.inOverlap
    ? { key: 'preview.cursor.overlap', params: { ms }, tone: 'warn' }
    : { key: 'preview.cursor.at', params: { time, ms }, tone: 'info' }
}
