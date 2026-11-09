import { useCallback, useEffect, useState } from 'react'
import {
  disconnectMowl,
  disconnectSwedebeat,
  getConnectionsView,
  setSwedebeatPreference,
  type Connection,
  type SwedebeatAccess,
} from '../../../api/connections'
import { getSpotifyStatus, type SpotifyStatus } from '../../../api/spotify'
import { useGuard } from '../../../auth/prompts'
import { useLayout } from '../../../hooks/useLayout'
import type { CatalogueKey } from '../../../i18n/en'
import { useT } from '../../../i18n/I18n'
import { t as translate } from '../../../i18n/t'
import { Link } from '../../../router/Router'
import { Notice, useAction } from '../../forms'
import { Button } from '../../ui/button'
import { forgetWarning } from '../mowlWarning'
import { ConfirmDisconnect } from './ConfirmDisconnect'
import { ConnectionRow } from './ConnectionRow'
import { MowlDialog } from './MowlDialog'
import { SwedebeatDialog } from './SwedebeatDialog'

type Which = 'mowl' | 'swedebeat'

const LAST: Record<Which, Record<string, CatalogueKey>> = {
  mowl: {
    needs_reconnect: 'mowl.last.needs_reconnect',
    decrypt_failed: 'mowl.last.decrypt_failed',
    rate_limited: 'mowl.last.rate_limited',
    upstream_unavailable: 'mowl.last.upstream_unavailable',
  },
  swedebeat: {
    needs_reconnect: 'swed.last.needs_reconnect',
    decrypt_failed: 'swed.last.decrypt_failed',
    rate_limited: 'swed.last.rate_limited',
    upstream_unavailable: 'swed.last.upstream_unavailable',
  },
}

/** The plain words for an ADR 0004 section 5 category; the raw code is never shown. */
function lastProblem(provider: string, category: string): string {
  const table = provider === 'swedebeat' ? LAST.swedebeat : LAST.mowl
  const known = Object.hasOwn(table, category) ? table[category] : undefined
  return translate(known ?? (provider === 'swedebeat' ? 'swed.last.other' : 'mowl.last.other'))
}
type Open = { kind: 'connect' | 'disconnect'; which: Which } | null

interface View {
  mowl: Connection | null
  swedebeat: Connection | null
  access: SwedebeatAccess
  qrEnabled: boolean
}

/**
 * Account, Connections (ADR 0028 section 3): MOWL, Spotify and Swedebeat as three rows. Each has a
 * status line and one button; Connect opens a dialog for that service, Disconnect asks first.
 * Spotify's set-up is its own page (the redirect to Spotify and back needs one), so its button
 * is a link to it. Swedebeat is always listed, as optional.
 */
export function ConnectionsList() {
  const t = useT()
  const guard = useGuard()
  const layout = useLayout()
  const action = useAction()
  const [view, setView] = useState<View | null>(null)
  const [spotify, setSpotify] = useState<SpotifyStatus | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [open, setOpen] = useState<Open>(null)
  const [note, setNote] = useState<string | null>(null)

  const show = useCallback((all: Awaited<ReturnType<typeof getConnectionsView>>) => {
    setView({
      mowl: all.connections.find((item) => item.provider === 'mowl') ?? null,
      swedebeat: all.connections.find((item) => item.provider === 'swedebeat') ?? null,
      access: all.swedebeat,
      qrEnabled: all.mowlQrEnabled,
    })
  }, [])
  const load = useCallback(async () => {
    show(await getConnectionsView())
  }, [show])

  useEffect(() => {
    const controller = new AbortController()
    getConnectionsView().then(
      (all) => {
        if (!controller.signal.aborted) show(all)
      },
      () => {
        if (!controller.signal.aborted) setLoadFailed(true)
      },
    )
    getSpotifyStatus(controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setSpotify(found)
      },
      () => undefined, // no Spotify row; the rest of the list still works
    )
    return () => {
      controller.abort()
    }
  }, [show])

  const close = () => {
    setOpen(null)
  }
  const connected = (which: Which) => (connection: Connection) => {
    setView((was) =>
      was === null
        ? was
        : {
            ...was,
            [which]: connection,
            access: which === 'swedebeat' ? { ...was.access, usesSwedebeat: true } : was.access,
          },
    )
    setNote(t(which === 'mowl' ? 'mowl.connected' : 'swed.connected'))
    close()
  }
  const disconnect = (which: Which) => {
    close()
    setNote(null)
    void action.run(async () => {
      if (which === 'mowl') await guard(() => disconnectMowl())
      else {
        await guard(() => disconnectSwedebeat())
        await setSwedebeatPreference(false).catch(() => undefined)
      }
      await load()
      setNote(t(which === 'mowl' ? 'mowl.disconnected' : 'swed.disconnected'))
    })
  }

  if (loadFailed) return <Notice kind="error">{t('mowl.loadFailed')}</Notice>
  if (view === null) return <p>{t('common.loading')}</p>

  const { mowl, swedebeat, access } = view
  const status = (found: Connection | null, purpose: string) =>
    found === null ? (
      purpose
    ) : found.status === 'connected' ? (
      <>
        <span className="font-medium text-ok" data-testid={`${found.provider}-status`}>
          {t('mowl.statusConnected')}
        </span>
        {found.account !== null ? (
          <>
            {' · '}
            <span data-testid={`${found.provider}-account`}>{found.account}</span>
          </>
        ) : null}
      </>
    ) : (
      <span data-testid={`${found.provider}-status`}>
        {t('mowl.statusAgain')}
        {found.lastError !== null ? ` · ${lastProblem(found.provider, found.lastError)}` : ''}
      </span>
    )
  const askDisconnect = (which: Which, label: string) => (
    <Button
      variant="secondary"
      aria-label={label}
      disabled={action.busy}
      onClick={() => {
        setOpen({ kind: 'disconnect', which })
      }}
    >
      {t('conn.disconnect')}
    </Button>
  )
  const askConnect = (which: Which, label: string, text: string) => (
    <Button
      variant="secondary"
      aria-label={label}
      onClick={() => {
        setOpen({ kind: 'connect', which })
      }}
    >
      {text}
    </Button>
  )
  const mowlOn = mowl?.status === 'connected'
  const sbOn = swedebeat?.status === 'connected'
  // The flag is on and the server holds Swedebeat's app key (ADR 0015); otherwise the row says so.
  const canConnectSwedebeat = access.enabled && access.available

  return (
    <section aria-labelledby="connections-title" className="flex flex-col gap-3">
      <h2 id="connections-title" className="m-0 font-display text-xl font-bold">
        {t('conn.title')}
      </h2>
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <ul className="m-0 flex list-none flex-col divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface p-0">
        <ConnectionRow
          testId="connection-mowl"
          mark="M"
          name={t('conn.mowl.name')}
          optional={false}
          status={status(mowl, t('conn.mowl.purpose'))}
          action={
            mowlOn
              ? askDisconnect('mowl', t('mowl.disconnect'))
              : askConnect('mowl', mowl ? t('mowl.connectAgainButton') : t('mowl.connectButton'), t('conn.connect'))
          }
        />
        {spotify !== null ? (
          <ConnectionRow
            testId="connection-spotify"
            mark="S"
            name={t('spot.title')}
            optional
            status={
              spotify.status === 'not_connected' ? (
                t('conn.spotify.purpose')
              ) : (
                <span data-testid="spotify-summary-status">
                  {spotify.status === 'connected' ? t('mowl.statusConnected') : t('mowl.statusAgain')}
                </span>
              )
            }
            action={
              <Button variant="secondary" asChild>
                <Link
                  to="/account/spotify"
                  aria-label={spotify.status === 'not_connected' ? t('spot.connect') : t('spot.manage')}
                >
                  {spotify.status === 'not_connected' ? t('conn.connect') : t('conn.manage')}
                </Link>
              </Button>
            }
          />
        ) : null}
        <ConnectionRow
          testId="connection-swedebeat"
          mark="Sb"
          name={t('swed.title')}
          optional
          status={
            !canConnectSwedebeat && swedebeat === null
              ? t('conn.swedebeat.unavailable')
              : status(swedebeat, t('conn.swedebeat.purpose'))
          }
          action={
            sbOn
              ? askDisconnect('swedebeat', t('swed.disconnect'))
              : canConnectSwedebeat
                ? askConnect('swedebeat', swedebeat ? t('swed.connectAgain') : t('swed.connect'), t('conn.connect'))
                : null
          }
        />
      </ul>

      <MowlDialog
        open={open?.kind === 'connect' && open.which === 'mowl'}
        qr={view.qrEnabled && layout !== 'phone'}
        again={mowl !== null}
        onClose={close}
        onConnected={connected('mowl')}
      />
      <SwedebeatDialog
        open={open?.kind === 'connect' && open.which === 'swedebeat'}
        wasUsing={access.usesSwedebeat}
        again={swedebeat !== null}
        onClose={close}
        onConnected={connected('swedebeat')}
      />
      <ConfirmDisconnect
        open={open?.kind === 'disconnect' && open.which === 'mowl'}
        title={t('conn.disconnectMowl')}
        text={forgetWarning()}
        yes={t('mowl.yesDisconnect')}
        busy={action.busy}
        onYes={() => {
          disconnect('mowl')
        }}
        onClose={close}
      />
      <ConfirmDisconnect
        open={open?.kind === 'disconnect' && open.which === 'swedebeat'}
        title={t('conn.disconnectSwedebeat')}
        text={t('swed.confirmText')}
        yes={t('swed.disconnectYes')}
        busy={action.busy}
        onYes={() => {
          disconnect('swedebeat')
        }}
        onClose={close}
      />
    </section>
  )
}
