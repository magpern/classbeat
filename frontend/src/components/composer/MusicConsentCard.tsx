import type { SpotifyStatus } from '../../api/spotify'
import { useT } from '../../i18n/I18n'
import { ConsentSwitch } from '../account/ConsentSwitch'

/**
 * Before the first song search (ADR 0038 amendment of 2026-11-24, decision 7; ADR 0008 section 2): the music is
 * decided and Spotify is connected, but the working playlist is not allowed yet. Says what classbeat will write,
 * with the existing consent switch (the same versioned consent and route as the Spotify page). Once it is on,
 * the driver starts the search by itself.
 */
export function MusicConsentCard({ status, onChanged }: { status: SpotifyStatus; onChanged: () => Promise<void> }) {
  const t = useT()
  return (
    <section aria-label={t('cw.music.consent.title')} data-testid="music-consent" className="flex flex-col gap-3 rounded-2xl border border-line bg-raised p-4">
      <h3 className="m-0 text-base font-semibold">{t('cw.music.consent.title')}</h3>
      <p className="m-0 text-[0.9375rem]">{t('cw.music.consent.body')}</p>
      <ConsentSwitch status={status} onChanged={onChanged} />
    </section>
  )
}
