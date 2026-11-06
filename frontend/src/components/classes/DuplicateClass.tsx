import { useId, useState, type FormEvent } from 'react'
import { getRevision } from '../../api/classes'
import { TITLE_MAX, copyTitle, duplicateClass, duplicateReasonText } from '../../api/classDuplicate'
import { useT } from '../../i18n/I18n'
import { contentHash } from '../../lib/canonical'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '../ui/dialog'

/**
 * Duplicate a class (ADR 0007 amendment of 2026-11-03, part D): a new class of the user's own with
 * one version copied as its version 1, under a name they can edit. The copy has no saved workout,
 * no approved songs and no conversation; nothing is sent to MOWL, Spotify or a model. It counts as
 * one class of the month. On success the page opens the new class.
 */
export function DuplicateClass({
  classId,
  title,
  revisionId,
  versionNumber,
  label,
}: {
  classId: string
  title: string
  /** The version to copy; `null` when the class has none yet (the action is then not offered). */
  revisionId: string | null
  versionNumber: number | null
  label?: string
}) {
  const t = useT()
  const { navigate } = useRouter()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(copyTitle(title))
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  // One key per opening of the dialog, so a double submit or a retry is the same request.
  const [key, setKey] = useState(newIdempotencyKey)
  const nameId = useId()
  const shown = label ?? t('class.duplicate.label')
  if (revisionId === null) return null

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setSending(true)
    void (async () => {
      try {
        // The hash of the content the server holds for this version: the copy goes through only
        // if the version is the one named (as Use this version does).
        const full = await getRevision(classId, revisionId)
        const made = await duplicateClass(classId, revisionId, await contentHash(full.raw), name.trim(), key)
        setOpen(false)
        navigate(`/classes/${made.classId}`)
      } catch (failure) {
        setError(duplicateReasonText(failure))
      } finally {
        setSending(false)
      }
    })()
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // While the request is in flight the dialog stays open (Escape, a click outside and the
        // close button do nothing): a reopen would make a new key and could make a second copy.
        if (!next && sending) return
        setOpen(next)
        if (next) {
          setName(copyTitle(title))
          setError(null)
          setKey(newIdempotencyKey())
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="secondary" aria-label={t('class.duplicate.trigger', { label: shown, title })}>
          {shown}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>{t('class.duplicate.title')}</DialogTitle>
        <DialogDescription>
          {versionNumber !== null
            ? t('class.duplicate.descriptionVersion', { number: versionNumber })
            : t('class.duplicate.descriptionThis')}
        </DialogDescription>
        <form onSubmit={submit} className="flex flex-col gap-3" aria-label={t('class.duplicate.title')}>
          <div className="field">
            <label htmlFor={nameId}>{t('class.duplicate.name')}</label>
            <input
              id={nameId}
              value={name}
              maxLength={TITLE_MAX}
              required
              onChange={(event) => {
                setName(event.target.value)
              }}
            />
          </div>
          {error !== null ? (
            <p role="alert" className="m-0">
              {error}
            </p>
          ) : null}
          <p className="m-0">
            <Button type="submit" disabled={sending || name.trim() === ''}>
              {sending ? t('class.duplicate.sending') : t('class.duplicate.label')}
            </Button>
          </p>
        </form>
      </DialogContent>
    </Dialog>
  )
}
