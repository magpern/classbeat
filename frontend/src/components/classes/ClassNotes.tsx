import type { ClassSummary } from '../../api/classes'
import { Trans, useT } from '../../i18n/I18n'

/** The notice over an older version that is not the current one: use it, or go back. */
export function NotCurrent({
  viewing,
  current,
  busy,
  working,
  onUse,
  onBack,
}: {
  viewing: number
  current: number | null
  /** The class has a job running. */
  busy: boolean
  /** The choice is being sent. */
  working: boolean
  onUse: () => void
  onBack: () => void
}) {
  const t = useT()
  return (
    <div className="notice" data-kind="info" role="status" data-testid="not-current">
      <p>{t('class.page.notCurrent', { viewing, current: current ?? '?' })}</p>
      <p>
        <button type="button" className="button" disabled={working || busy} onClick={onUse}>
          {t('class.useThisVersion')}
        </button>
        <button type="button" className="button button--secondary" onClick={onBack}>
          {t('class.page.back', { number: current ?? '?' })}
        </button>
      </p>
      {busy ? <p className="muted">{t('class.waitRunning')}</p> : null}
    </div>
  )
}

/** The playlist the class's songs were added to, with a link to Spotify when there is one. */
export function PlaylistLine({ playlist }: { playlist: NonNullable<ClassSummary['playlist']> }) {
  const t = useT()
  return (
    <p data-testid="playlist" className="m-0">
      <Trans id="class.page.playlist" slots={{ name: <strong>{playlist.name}</strong> }} />
      {playlist.link !== null ? (
        <>
          {' '}
          <a href={playlist.link} target="_blank" rel="noopener noreferrer">
            {t('class.page.openSpotify')}
          </a>
        </>
      ) : null}
    </p>
  )
}
