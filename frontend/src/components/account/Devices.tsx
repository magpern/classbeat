import { useCallback, useEffect, useState } from 'react'
import { listDevices, signOutDevice, type DeviceSession } from '../../api/auth'
import { Notice, Section, useAction } from '../forms'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'

function when(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

/**
 * Signed-in devices (ADR 0003 section 3, ADR 0013 section 5): the user's own sessions with the
 * browser they came from and when they were last used (never the IP address), sign out one of
 * the others, or everywhere (`onSignOutEverywhere`, the same `POST /api/auth/logout-all`).
 */
export function Devices({ busy, onSignOutEverywhere }: { busy: boolean; onSignOutEverywhere: () => void }) {
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
    <Section title="Signed-in devices">
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {failed ? <p className="text-muted">The devices could not be loaded. Reload the page to try again.</p> : null}
      {devices === null && !failed ? <p>Loading</p> : null}
      {devices !== null ? (
        <ul className="m-0 flex list-none flex-col gap-2 p-0" data-testid="devices">
          {devices.map((device) => (
            <li key={device.id} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 break-words">
                {device.userAgent ?? 'Unknown browser'}
                <span className="block text-muted">Last used {when(device.lastSeenAt)}</span>
              </span>
              {device.current ? (
                <Chip tone="ok">This device</Chip>
              ) : (
                <Button
                  variant="secondary"
                  disabled={action.busy}
                  onClick={() => {
                    signOut(device.id)
                  }}
                >
                  Sign out this device
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      <p>
        <Button variant="secondary" disabled={busy} onClick={onSignOutEverywhere}>
          Sign out everywhere
        </Button>
      </p>
    </Section>
  )
}
