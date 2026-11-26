import { useState } from 'react'
import type { MusicDecision } from '../../api/composerState'
import type { MusicSetup as Setup } from '../../hooks/composer/useMusicSetup'
import { Trans, useT } from '../../i18n/I18n'
import type { MusicGate } from '../../lib/composer/musicGate'
import { Link, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { createError } from '../newclass/createError'
import { MusicConsentCard } from './MusicConsentCard'
import { MusicQuestion } from './MusicQuestion'
import { startWithoutMusic } from './startWithoutMusic'

/**
 * What the composer workspace shows about the music while the song search waits (ADR 0038 amendment of
 * 2026-11-24, decisions 5 to 8): the question while it is undecided (also after closing and reopening, until it
 * is answered; "Decide later" only closes it for now), then, once it is decided, the way on to the search:
 * connect Spotify, or allow the working playlist. Nothing here starts the search; the driver does, once the gate
 * is `ready`.
 */
export function MusicSetup({
  classId,
  gate,
  decision,
  setup,
  words,
  pinned,
  reload,
}: {
  classId: string
  gate: MusicGate
  decision: MusicDecision | null
  setup: Setup
  /** The request's words, for "Continue without music". */
  words: string | null
  /** How many songs the instructor named: the only hint an undecided class can have, so "Choose it for me" needs one. */
  pinned: number
  reload: () => void
}) {
  const t = useT()
  const { navigate } = useRouter()
  const [later, setLater] = useState(false)
  const without = useAction()
  if (decision === null) return null
  if (gate === 'undecided') {
    // The question waits for Spotify's state, so "Connect Spotify" and "Continue without music" show right at once.
    if (later || setup.songs === null) return null
    const mowl = setup.mowlConnectionId
    return (
      <MusicQuestion
        classId={classId}
        offered={setup.offered}
        connected={setup.songs.kind !== 'needs_spotify'}
        soundlessEnabled={setup.soundlessEnabled}
        canPick={pinned > 0}
        canStartWithout={words !== null && mowl !== null}
        onAnswered={reload}
        onLater={() => {
          setLater(true)
          reload()
        }}
        onWithoutMusic={() => {
          if (words === null || mowl === null) return
          void without.run(
            async () => {
              navigate(`/classes/${await startWithoutMusic(words, mowl)}`)
            },
            (error) => createError(t, error, true),
          )
        }}
        withoutError={without.error}
        withoutBusy={without.busy}
      />
    )
  }
  if (gate === 'not_connected') {
    return (
      <Notice>
        <span data-testid="music-connect">
          <Trans id="music.needsSpotify" slots={{ link: <Link to="/account/spotify">{t('music.connectSpotify')}</Link> }} />
        </span>
      </Notice>
    )
  }
  if (gate === 'consent_missing') {
    // The card's own place is kept while the status loads, so the page does not jump when it arrives.
    if (setup.status === null) {
      return (
        <p className="m-0 text-muted" role="status" aria-busy="true" data-testid="music-consent-loading">
          {t('common.loading')}
        </p>
      )
    }
    return (
      <MusicConsentCard
        status={setup.status}
        onChanged={() => {
          setup.refresh()
          return Promise.resolve()
        }}
      />
    )
  }
  if (gate === 'unavailable') return <Notice>{t('start.spotify.unavailable')}</Notice>
  return null
}
