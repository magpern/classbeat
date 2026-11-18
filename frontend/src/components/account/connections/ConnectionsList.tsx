import { useCallback, useEffect, useState } from 'react'
import {
  disconnectMowl,
  disconnectSwedebeat,
  getConnectionsView,
  setSwedebeatPreference,
  type Connection,
  type SwedebeatAccess,
} from '../../../api/connections'
import { useGuard } from '../../../auth/prompts'
import { useLayout } from '../../../hooks/useLayout'
import { useRouter } from '../../../router/Router'
import type { SpotifyStatus } from '../../../api/spotify'
import { useT } from '../../../i18n/I18n'
import { Notice, useAction } from '../../forms'
import { Button } from '../../ui/button'
import { forgetWarning } from '../mowlWarning'
import { ConfirmDisconnect } from './ConfirmDisconnect'
import { describeConnection, type Which } from './connectionStatus'
import { ConnectionTile } from './ConnectionTile'
import { MowlDialog } from './MowlDialog'
import { OneStepLeft } from './OneStepLeft'
import { PlayerRow } from './PlayerRow'
import { SPOTIFY_PATH, SpotifyRow } from './SpotifyRow'
import { SwedebeatDialog } from './SwedebeatDialog'

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
 * Spotify's dialog is also opened by /account/spotify, where Spotify's callback returns
 * (SpotifyRow). classbeat-player has its own row and dialog (PlayerRow), opened by /account/player.
 * Swedebeat is always listed, as optional.
 */
export function ConnectionsList({ onLinked }: { onLinked?: (count: number) => void }) {
  const t = useT()
  const { navigate } = useRouter()
  const guard = useGuard()
  const layout = useLayout()
  const action = useAction()
  const [view, setView] = useState<View | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [open, setOpen] = useState<Open>(null)
  const [note, setNote] = useState<string | null>(null)
  // Spotify's row loads its own status; null while unknown or when song picking is off.
  const [spotify, setSpotify] = useState<SpotifyStatus['status'] | null>(null)
  const mowlLinked = view === null ? null : view.mowl?.status === 'connected'
  useEffect(() => {
    if (mowlLinked !== null) onLinked?.((mowlLinked ? 1 : 0) + (spotify === 'connected' ? 1 : 0))
  }, [mowlLinked, spotify, onLinked])

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

  if (loadFailed)
    // Spotify's row loads its own status and owns its dialog, so a link to /account/spotify (the
    // return from Spotify) still opens it when the list could not be loaded.
    return (
      <section aria-labelledby="connections-title" className="flex flex-col gap-3">
        <h2 id="connections-title" className="m-0 font-display text-2xl font-extrabold">
          {t('conn.title')}
        </h2>
        <Notice kind="error">{t('mowl.loadFailed')}</Notice>
        <ul className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(min(18rem,100%),1fr))] gap-4 p-0 empty:hidden">
          <SpotifyRow />
          <PlayerRow />
        </ul>
      </section>
    )
  if (view === null) return <p>{t('common.loading')}</p>

  const { mowl, swedebeat, access } = view
  const words = {
    connected: t('mowl.statusConnected'),
    again: t('mowl.statusAgain'),
    none: t('conn.notConnected'),
  }
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
  // The first required connection still missing (MOWL, then Spotify when it is offered).
  const step = !mowlOn
    ? {
        missing: t('conn.mowl.name'),
        linked: spotify === 'connected' ? 1 : 0,
        onConnect: () => {
          setOpen({ kind: 'connect', which: 'mowl' })
        },
      }
    : spotify !== null && spotify !== 'connected'
      ? {
          missing: t('spot.title'),
          linked: 1,
          onConnect: () => {
            navigate(SPOTIFY_PATH)
          },
        }
      : null
  const sbOn = swedebeat?.status === 'connected'
  // The flag is on and the server holds Swedebeat's app key (ADR 0015); otherwise the row says so.
  const canConnectSwedebeat = access.enabled && access.available

  return (
    <section aria-labelledby="connections-title" className="flex flex-col gap-3">
      {step !== null ? <OneStepLeft {...step} /> : null}
      <h2 id="connections-title" className="m-0 font-display text-2xl font-extrabold">
        {t('conn.title')}
      </h2>
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <ul className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(min(18rem,100%),1fr))] gap-4 p-0">
        <ConnectionTile
          testId="connection-mowl"
          mark="M"
          name={t('conn.mowl.name')}
          optional={false}
          {...describeConnection(mowl, t('conn.mowl.purpose'), words)}
          action={
            mowlOn
              ? askDisconnect('mowl', t('mowl.disconnect'))
              : askConnect('mowl', mowl ? t('mowl.connectAgainButton') : t('mowl.connectButton'), t('conn.connect'))
          }
        />
        <SpotifyRow onStatus={setSpotify} />
        <PlayerRow />
        <ConnectionTile
          testId="connection-swedebeat"
          mark="Sb"
          name={t('swed.title')}
          optional
          {...describeConnection(
            swedebeat,
            !canConnectSwedebeat && swedebeat === null ? t('conn.swedebeat.unavailable') : t('conn.swedebeat.purpose'),
            words,
          )}
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
