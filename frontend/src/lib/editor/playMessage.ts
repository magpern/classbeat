import type { CatalogueKey } from '../../i18n/en'
import type { TransportState } from '../../preview/transport'

// What the audio preview says in words for a transport state (pure). Never a raw code: every reason
// the transport can give maps to a catalogue key, and an unknown one to the general failure.

export interface PlayMessage {
  key: CatalogueKey
  tone: 'info' | 'error'
  /** A Try again button makes sense: the session lives and a seek asks for the clip again. */
  retry: boolean
}

const ERRORS = ['unavailable', 'rate_limited', 'render_failed', 'draft_changed', 'network', 'blocked', 'failed'] as const
type ErrorReason = (typeof ERRORS)[number]

export function playMessage(state: TransportState): PlayMessage | null {
  if (state.status === 'paused' && state.reason === 'limit') return { key: 'editor.play.limit', tone: 'info', retry: false }
  if (state.status === 'paused' && state.reason === 'session_ended') return { key: 'editor.play.sessionEnded', tone: 'info', retry: false }
  if (state.status === 'starting') {
    const key = state.reason === 'rendering' ? 'editor.play.rendering' : state.reason === 'next_part' ? 'editor.play.nextPart' : 'editor.play.starting'
    return { key, tone: 'info', retry: false }
  }
  if (state.status === 'error') {
    const known = ERRORS.find((r) => r === state.reason)
    const reason: ErrorReason = known ?? 'failed'
    return {
      key: `editor.play.err.${reason}`,
      tone: 'error',
      retry: reason !== 'unavailable',
    }
  }
  return null
}
