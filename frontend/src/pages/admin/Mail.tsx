import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { adminError } from '../../api/admin'
import {
  clearMailSetting,
  getMail,
  previewUrl,
  sendTest,
  setMailSetting,
  type MailKey,
  type MailKind,
  type MailView,
} from '../../api/adminMail'
import { asText, type Flag } from '../../api/adminSettings'
import { SelectField } from '../../components/classes/fields'
import { Field, Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { Switch } from '../../components/ui/switch'
import { isReason, useAdminWrite, useMfaRequired } from './StepUp'
import { SectionTitle } from './Table'

const BRANDING: { key: MailKey; title: string; label: string; hint: string; max: number; type?: string }[] = [
  { key: 'mail_sender_name', title: 'Sender name', label: 'Sender name', hint: 'Shown as the sender of every mail, at most 60 characters.', max: 60 },
  { key: 'mail_support_email', title: 'Support address', label: 'Support address', hint: 'Where replies and help requests go. A single address.', max: 254, type: 'email' },
  { key: 'mail_footer', title: 'Footer', label: 'Footer text', hint: 'Plain text under every mail, at most 280 characters.', max: 280 },
]

/**
 * Mail (ADR 0022): the branding of every mail, a switch for each optional kind (essential kinds
 * are locked on), and a preview of every kind with a test send to the admin's own address. The
 * preview is a sandboxed iframe of a server page with its own policy; its text is never put into
 * this page.
 */
export function Mail() {
  const onMfaRequired = useMfaRequired()
  const [view, setView] = useState<MailView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    getMail(controller.signal).then(
      (found) => {
        setError(null)
        setView(found)
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [round, onMfaRequired])

  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])

  return (
    <section aria-labelledby="admin-mail-title">
      <SectionTitle id="admin-mail-title">Mail</SectionTitle>
      <p className="m-0 mb-4 text-muted">
        How classbeat&apos;s mail looks and which optional mails are sent. Mails people need to sign in or keep their
        account safe are always sent.
      </p>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {view === null && error === null ? <p>Loading</p> : null}
      {view !== null ? (
        <div className="flex flex-col gap-4">
          {BRANDING.map((spec) => {
            const item = view.settings.find((s) => s.key === spec.key)
            return item === undefined ? null : <BrandingCard key={spec.key} spec={spec} item={item} onChanged={reload} />
          })}
          <Switches kinds={view.kinds} onChanged={reload} />
          <Preview kinds={view.kinds} />
        </div>
      ) : null}
    </section>
  )
}

function BrandingCard({ spec, item, onChanged }: { spec: (typeof BRANDING)[number]; item: Flag; onChanged: () => void }) {
  const write = useAdminWrite()
  const action = useAction()
  const [value, setValue] = useState(asText(item.stored) ?? '')
  const save = (task: () => Promise<Flag>) => {
    void action.run(() => write(task), adminError).then((saved) => {
      if (saved !== undefined) onChanged()
    })
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    save(() => setMailSetting(spec.key, value))
  }
  const effective = asText(item.effective)
  return (
    <Section title={spec.title}>
      <p className="m-0 mb-2" data-testid={`mail-${spec.key}`}>
        Now: <strong>{effective ?? 'none'}</strong>{' '}
        <Chip tone={item.source === 'database' ? 'ok' : 'neutral'}>
          {item.source === 'database' ? 'set here' : 'default'}
        </Chip>
      </p>
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit} aria-label={`Change the ${spec.title.toLowerCase()}`} className="flex flex-wrap items-end gap-2">
        <Field
          label={spec.label}
          hint={spec.hint}
          type={spec.type ?? 'text'}
          maxLength={spec.max}
          required
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
          }}
        />
        <Button type="submit" disabled={action.busy}>
          Save
        </Button>
        {item.stored !== null ? (
          <Button
            variant="ghost"
            disabled={action.busy}
            onClick={() => {
              setValue('')
              save(() => clearMailSetting(spec.key))
            }}
          >
            Use the default
          </Button>
        ) : null}
      </form>
    </Section>
  )
}

function Switches({ kinds, onChanged }: { kinds: MailKind[]; onChanged: () => void }) {
  const write = useAdminWrite()
  const action = useAction()
  const toggle = (kind: MailKind, enabled: boolean) => {
    const off = kinds.filter((k) => !k.enabled && k.id !== kind.id).map((k) => k.id)
    if (!enabled) off.push(kind.id)
    void action.run(() => write(() => setMailSetting('mail_kinds_disabled', off)), adminError).then((saved) => {
      if (saved !== undefined) onChanged()
    })
  }
  return (
    <Section title="Notifications">
      <p className="m-0 mb-2 text-muted">
        A kind that is switched off is not sent. You can still preview it and send yourself a test.
      </p>
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {kinds.map((kind) => (
          <li key={kind.id} className="flex flex-wrap items-center gap-2">
            {kind.adminCanDisable ? (
              <Switch
                id={`kind-${kind.id}`}
                checked={kind.enabled}
                disabled={action.busy}
                onCheckedChange={(next) => {
                  toggle(kind, next)
                }}
              />
            ) : (
              <Switch id={`kind-${kind.id}`} checked disabled aria-describedby={`locked-${kind.id}`} />
            )}
            <label htmlFor={`kind-${kind.id}`}>{kind.id.replaceAll('_', ' ')}</label>
            {kind.adminCanDisable ? null : (
              <span id={`locked-${kind.id}`} className="text-[0.9375rem] text-muted">
                Always sent
              </span>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Preview({ kinds }: { kinds: MailKind[] }) {
  const write = useAdminWrite()
  const action = useAction()
  const [kind, setKind] = useState(kinds[0]?.id ?? '')
  const [part, setPart] = useState<'html' | 'text'>('html')
  const [sent, setSent] = useState(false)
  return (
    <Section title="Preview">
      <div className="flex flex-wrap items-end gap-2">
        <SelectField
          label="Mail to preview"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value)
            setSent(false)
          }}
        >
          {kinds.map((k) => (
            <option key={k.id} value={k.id}>
              {k.id.replaceAll('_', ' ')}
            </option>
          ))}
        </SelectField>
        <div role="group" aria-label="Show" className="flex gap-2">
          <Button variant={part === 'html' ? 'primary' : 'ghost'} aria-pressed={part === 'html'} onClick={() => { setPart('html') }}>
            HTML
          </Button>
          <Button variant={part === 'text' ? 'primary' : 'ghost'} aria-pressed={part === 'text'} onClick={() => { setPart('text') }}>
            Text
          </Button>
        </div>
        <Button
          disabled={action.busy || kind === ''}
          onClick={() => {
            setSent(false)
            void action.run(() => write(() => sendTest(kind)), adminError).then(() => {
              setSent(true)
            })
          }}
        >
          Send test to me
        </Button>
      </div>
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      {sent && action.error === null ? <Notice kind="success">Sent to your own address.</Notice> : null}
      {kind !== '' ? (
        <iframe
          key={`${kind}-${part}`}
          title="Mail preview"
          sandbox=""
          src={previewUrl(kind, part)}
          className="mt-3 h-[32rem] w-full rounded border border-line bg-surface"
        />
      ) : null}
    </Section>
  )
}
