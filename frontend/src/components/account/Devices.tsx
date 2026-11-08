import { useCallback, useEffect, useState } from 'react'
import { listDevices, signOutDevice, type DeviceSession } from '../../api/auth'
import { Notice, Section, useAction } from '../forms'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'
import { dateTime } from '../../i18n/format'
import { useT } from '../../i18n/I18n'

function when(iso: string): string {
  return dateTime(iso)
}

/**
 * Signed-in devices (ADR 0003 section 3, ADR 0013 section 5): the user's own sessions with the
 * browser they came from and when they were last used (never the IP address), sign out one of
 * the others, or everywhere (`onSignOutEverywhere`, the same `POST /api/auth/logout-all`).
 * A session seen from a new network or browser is marked "New location" (#632) until it signs in
 * again there; it is never signed out for that.
 */
export function Devices({ busy, onSignOutEverywhere }: { busy: boolean; onSignOutEverywhere: () => void }) {
  const t = useT()
  const [devices, setDevices] = useState<DeviceSession[] | null>(null)
  const [failed, setFailed] = useState(false)
  const action = useAction()

  const load = useCallback(async () => {
    setDevices(await listDevices())
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    listDevices(controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setDevices(found)
      },
      () => {
        if (!controller.signal.aborted) setFailed(true)
      },
    )
    return () => {
      controller.abort()
    }
  }, [])

  const signOut = (id: string) => {
    void action.run(async () => {
      await signOutDevice(id)
      await load()
    })
  }

  return (
    <Section title={t('devices.title')}>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {failed ? <p className="text-muted">{t('devices.loadFailed')}</p> : null}
      {devices === null && !failed ? <p>{t('common.loading')}</p> : null}
      {devices !== null ? (
        <ul className="m-0 flex list-none flex-col gap-2 p-0" data-testid="devices">
          {devices.map((device) => (
            <li key={device.id} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 break-words">
                {device.userAgent ?? t('devices.unknownBrowser')}
                <span className="block text-muted">
                  {t('devices.lastUsed', { when: when(device.lastSeenAt) })}
                </span>
                {device.locationChangedAt ? (
                  <span className="block text-muted" data-testid="device-moved">
                    {t(device.current ? 'devices.movedCurrent' : 'devices.movedOther', {
                      when: when(device.locationChangedAt),
                    })}
                  </span>
                ) : null}
              </span>
              {device.locationChangedAt ? <Chip tone="warn">{t('devices.newLocation')}</Chip> : null}
              {device.current ? (
                <Chip tone="ok">{t('devices.thisDevice')}</Chip>
              ) : (
                <Button
                  variant="secondary"
                  disabled={action.busy}
                  onClick={() => {
                    signOut(device.id)
                  }}
                >
                  {t('devices.signOutThis')}
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      <p>
        <Button variant="secondary" disabled={busy} onClick={onSignOutEverywhere}>
          {t('devices.signOutAll')}
        </Button>
      </p>
    </Section>
  )
}
