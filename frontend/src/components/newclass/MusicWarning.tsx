import { Trans, useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { FieldCard } from '../start/FieldCard'

/**
 * The missing MOWL connection as a warning card inside "Music from", with the connect link (ADR 0013 Afterburn A3):
 * the page around it stays. The sentences are the ones the bare notice used.
 */
export function MusicWarning({ reconnect }: { reconnect: boolean }) {
  const t = useT()
  return (
    <FieldCard title={t('start.music.from')}>
      <p role="status" className="m-0 flex items-center gap-3 rounded-2xl bg-warn-soft py-2 pl-3 pr-2 text-[0.9375rem] text-text">
        <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-warn" />
        <span className="min-w-0 flex-1">
          <Trans
            id={reconnect ? 'newclass.noMowl.reconnect' : 'newclass.noMowl.connect'}
            slots={{
              link: <Link to="/account">{t(reconnect ? 'newclass.noMowl.reconnectLink' : 'newclass.noMowl.connectLink')}</Link>,
            }}
          />
        </span>
      </p>
    </FieldCard>
  )
}
