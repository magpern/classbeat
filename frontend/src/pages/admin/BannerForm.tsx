import { useState, type FormEvent } from 'react'
import { asBanner, type BannerValue, type Flag } from '../../api/adminSettings'
import { SelectField, TextAreaField } from '../../components/classes/fields'
import { Button } from '../../components/ui/button'

/**
 * The banner card's form. The banner has no environment value, so "clear" means "no banner":
 * it gets its own "Remove banner" button (shown only while one is stored) instead of the generic
 * "Use the environment's value". The text is required (1 to 280 characters), so saving an empty
 * text is not a way to remove it.
 */
export function BannerForm({
  item,
  busy,
  onSave,
  onRemove,
}: {
  item: Flag
  busy: boolean
  onSave: (v: BannerValue) => void
  onRemove: () => void
}) {
  const stored = asBanner(item.stored)
  const [words, setWords] = useState(stored?.text ?? '')
  const [level, setLevel] = useState(stored?.level ?? 'info')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave({ text: words, level })
  }
  return (
    <form onSubmit={submit} aria-label="Change the banner" className="flex flex-col gap-2">
      <TextAreaField
        label="Banner text"
        hint="Shown on every page as plain text, at most 280 characters."
        maxLength={280}
        required
        value={words}
        onChange={(e) => {
          setWords(e.target.value)
        }}
      />
      <div className="flex flex-wrap items-end gap-2">
        <SelectField label="Banner level" value={level} onChange={(e) => { setLevel(e.target.value) }}>
          <option value="info">Information</option>
          <option value="warning">Warning</option>
        </SelectField>
        <Button type="submit" disabled={busy}>
          Save
        </Button>
        {item.stored !== null ? (
          <Button type="button" variant="secondary" disabled={busy} onClick={onRemove}>
            Remove banner
          </Button>
        ) : null}
      </div>
    </form>
  )
}
