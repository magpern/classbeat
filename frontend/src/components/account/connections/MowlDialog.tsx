import { useCallback, useState, type FormEvent } from 'react'
import { ApiError } from '../../../api/client'
import { connectMowl, type Connection } from '../../../api/connections'
import { errorMessage } from '../../../api/messages'
import { useGuard } from '../../../auth/prompts'
import type { CatalogueKey } from '../../../i18n/en'
import { useT } from '../../../i18n/I18n'
import { t as translate } from '../../../i18n/t'
import { Field, Notice, useAction } from '../../forms'
import { Button } from '../../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../ui/tabs'
import { MowlQr } from '../MowlQr'
import { MowlSteps } from './MowlSteps'
import { forgetWarning } from '../mowlWarning'

// Plain words for the ADR 0004 section 5 categories. The raw code is never shown.
const CONNECT_ERRORS: Record<string, CatalogueKey> = {
  invalid_credentials: 'mowl.err.invalid_credentials',
  upstream_unavailable: 'mowl.err.upstream_unavailable',
  unknown: 'mowl.err.unknown',
}

function connectError(error: unknown): string {
  if (error instanceof ApiError && error.status !== 429 && error.reason !== null) {
    const known = Object.hasOwn(CONNECT_ERRORS, error.reason) ? CONNECT_ERRORS[error.reason] : undefined
    if (known !== undefined) return translate(known)
  }
  return errorMessage(error)
}

/**
 * Connect MOWL (ADR 0028 section 3). With the QR on and a layout wider than a phone's the dialog
 * opens on "Scan code" (ADR 0024: a phone cannot scan its own screen); otherwise it is the
 * password form alone. The password is sent once and is not kept on the page.
 */
export function MowlDialog({
  open,
  qr,
  again,
  onClose,
  onConnected,
}: {
  open: boolean
  /** The QR tab is offered: the server allows it and the layout is not a phone's. */
  qr: boolean
  /** The user already has a connection that needs to be made again. */
  again: boolean
  onClose: () => void
  onConnected: (connection: Connection) => void
}) {
  const t = useT()
  const guard = useGuard()
  const action = useAction()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [tab, setTab] = useState<'scan' | 'password'>('scan')

  const connected = useCallback(
    (connection: Connection) => {
      onConnected(connection)
    },
    [onConnected],
  )

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const sent = { email: email.trim(), password }
    void action
      .run(async () => {
        connected(await guard(() => connectMowl(sent.email, sent.password)))
      }, connectError)
      .finally(() => {
        setPassword('') // the password is not kept on the page after the attempt
      })
    setEmail(sent.email)
  }

  const form = (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field
        label={t('mowl.emailLabel')}
        type="email"
        autoComplete="username"
        value={email}
        onChange={(e) => {
          setEmail(e.target.value)
        }}
        required
      />
      <Field
        label={t('mowl.passwordLabel')}
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(e) => {
          setPassword(e.target.value)
        }}
        required
      />
      <Button type="submit" disabled={action.busy}>
        {again ? t('mowl.connectAgainButton') : t('mowl.connectButton')}
      </Button>
    </form>
  )

  const header = (
    <div className="flex flex-col gap-2">
      <DialogTitle className="font-display text-3xl font-extrabold">{t('mowl.connectButton')}</DialogTitle>
      <DialogDescription>
        {again ? t('mowl.reconnectWarning', { warning: forgetWarning() }) : t('conn.noPassword')}
      </DialogDescription>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
    </div>
  )

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          action.clear()
          onClose()
        }
      }}
    >
      <DialogContent className={qr ? 'w-[min(46rem,calc(100%-2rem))] p-0' : undefined}>
        {qr ? (
          <Tabs
            value={tab}
            onValueChange={(value) => {
              setTab(value === 'password' ? 'password' : 'scan')
            }}
            className="grid md:grid-cols-2"
          >
            <div className="flex flex-col gap-5 p-6 sm:p-8">
              {header}
              <TabsList aria-label={t('conn.mowl.ways')} className="w-fit">
                <TabsTrigger value="scan">{t('conn.tab.scan')}</TabsTrigger>
                <TabsTrigger value="password">{t('conn.tab.password')}</TabsTrigger>
              </TabsList>
              <TabsContent value="scan" className="p-0">
                <MowlSteps />
              </TabsContent>
              <TabsContent value="password" className="p-0">
                {form}
              </TabsContent>
            </div>
            {tab === 'scan' ? (
              <div className="p-6 pt-0 sm:p-8 md:pt-8">
                <MowlQr autoStart onConnected={connected} />
              </div>
            ) : null}
          </Tabs>
        ) : (
          <div className="flex flex-col gap-5">
            {header}
            {form}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
