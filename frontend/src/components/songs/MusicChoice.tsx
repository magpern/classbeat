import { Trans, useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { ChipButton } from '../ui/chip'

export type Music = 'playlist' | 'pick' | 'swedebeat_mix' | 'none'

/** What "Pick songs for me" needs (ADR 0008 section 9). `off`: the feature is hidden. */
export type SongsAvailability =
  | { kind: 'off' }
  | { kind: 'needs_spotify' }
  | { kind: 'needs_consent' }
  | { kind: 'ready'; spotifyConnectionId: string }

/**
 * Whether "Use my Swedebeat mix" is offered (ADR 0016 section 3). The new-class page renders the form
 * only with a connected MOWL account, so there is no "needs MOWL" state here.
 */
export type SwedebeatMixAvailability =
  | { kind: 'off' }
  | { kind: 'ready' }
  | { kind: 'failed'; message: string }

/**
 * The music choice of the new-class form: a playlist from MOWL, song picking, or a Swedebeat mix.
 * Without Spotify and the working-playlist consent the second choice is not selectable and links
 * to the wizard; with song picking switched off nothing is shown. The Swedebeat mix choice needs
 * the effective `swedebeat_enabled` flag (the page itself needs the MOWL connection). "No music"
 * (a soundless class, ADR 0007 amendment of 2026-11-03) is offered only when `soundless` is true:
 * the page passes the effective `soundless_enabled` flag and never offers it for a running class.
 */
export function MusicChoice({
  songs,
  swedebeat,
  soundless = false,
  value,
  onChange,
}: {
  songs: SongsAvailability
  swedebeat: SwedebeatMixAvailability
  /** Whether "No music" is offered: the effective `soundless_enabled` flag, cycling only. */
  soundless?: boolean
  value: Music
  onChange: (music: Music) => void
}) {
  const t = useT()
  if (songs.kind === 'off' && swedebeat.kind === 'off' && !soundless) return null
  const ready = songs.kind === 'ready'
  const swedebeatReady = swedebeat.kind === 'ready'
  return (
    <div className="flex flex-col gap-2" role="group" aria-labelledby="music-label">
      <span id="music-label" className="text-[0.9375rem] font-medium text-text">
        {t('music.label')}
      </span>
      <div className="flex flex-wrap gap-2">
        <ChipButton
          pressed={value === 'playlist'}
          onClick={() => {
            onChange('playlist')
          }}
        >
          {t('music.playlist')}
        </ChipButton>
        {songs.kind !== 'off' ? (
          <ChipButton
            pressed={value === 'pick'}
            disabled={!ready}
            onClick={() => {
              onChange('pick')
            }}
          >
            {t('music.pick')}
          </ChipButton>
        ) : null}
        {swedebeat.kind !== 'off' ? (
          <ChipButton
            pressed={value === 'swedebeat_mix'}
            disabled={!swedebeatReady}
            onClick={() => {
              onChange('swedebeat_mix')
            }}
          >
            {t('music.swedebeat')}
          </ChipButton>
        ) : null}
        {soundless ? (
          <ChipButton
            pressed={value === 'none'}
            onClick={() => {
              onChange('none')
            }}
          >
            {t('music.none')}
          </ChipButton>
        ) : null}
      </div>
      {songs.kind === 'needs_spotify' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          <Trans
            id="music.needsSpotify"
            slots={{ link: <Link to="/account/spotify">{t('music.connectSpotify')}</Link> }}
          />
        </p>
      ) : null}
      {songs.kind === 'needs_consent' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          <Trans
            id="music.needsConsent"
            slots={{ link: <Link to="/account/spotify">{t('music.openSettings')}</Link> }}
          />
        </p>
      ) : null}
      {swedebeat.kind === 'failed' ? (
        <p className="m-0 text-[0.9375rem] text-muted" role="status">
          {swedebeat.message}
        </p>
      ) : null}
      {ready && value === 'pick' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          {t('music.pickHint')}
        </p>
      ) : null}
      {soundless && value === 'none' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          {t('music.noneHint')}
        </p>
      ) : null}
      {swedebeatReady && value === 'swedebeat_mix' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          {t('music.swedebeatHint')}
        </p>
      ) : null}
    </div>
  )
}
