import { useState } from 'react'
import type { Passkey } from '../../api/passkeys'
import { useT } from '../../i18n/I18n'
import { date } from '../../i18n/format'
import { Field } from '../forms'

interface Props {
  passkey: Passkey
  busy: boolean
  /** Rename is a write the flag switches off (404 `feature_off`); remove is not. */
  canRename: boolean
  onRename: (id: string, label: string) => void
  onRemove: (id: string) => void
}

/** One passkey: label, where it lives and when it was last used, with rename and remove. */
export function PasskeyRow({ passkey, busy, canRename, onRename, onRemove }: Props) {
  const t = useT()
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState(passkey.label)

  if (editing) {
    return (
      <li>
        <form
          className="passkey__rename"
          onSubmit={(event) => {
            event.preventDefault()
            onRename(passkey.id, label.trim())
            setEditing(false)
          }}
        >
          <Field
            label={t('passkey.nameLabel')}
            value={label}
            maxLength={64}
            required
            onChange={(event) => {
              setLabel(event.target.value)
            }}
          />
          <button type="submit" className="button" disabled={busy || label.trim() === ''}>
            {t('passkey.save')}
          </button>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => {
              setLabel(passkey.label)
              setEditing(false)
            }}
          >
            {t('common.cancel')}
          </button>
        </form>
      </li>
    )
  }
  return (
    <li>
      <span>
        {passkey.label}
        <span className="muted">
          {' '}
          {passkey.synced ? t('passkey.synced') : t('passkey.deviceOnly')}
          {' · '}
          {passkey.lastUsedAt === null
            ? t('passkey.neverUsed')
            : t('passkey.lastUsed', { date: date(passkey.lastUsedAt) })}
        </span>
      </span>
      <span>
        {canRename ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            onClick={() => {
              setEditing(true)
            }}
          >
            {t('passkey.rename', { name: passkey.label })}
          </button>
        ) : null}{' '}
        <button
          type="button"
          className="button button--secondary"
          disabled={busy}
          onClick={() => {
            onRemove(passkey.id)
          }}
        >
          {t('passkey.remove', { name: passkey.label })}
        </button>
      </span>
    </li>
  )
}
