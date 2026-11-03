import { useId, useState, type FormEvent } from 'react'
import { getRevision } from '../../api/classes'
import { TITLE_MAX, copyTitle, duplicateClass, duplicateReasonText } from '../../api/classDuplicate'
import { contentHash } from '../../lib/canonical'
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
  label = 'Duplicate',
}: {
  classId: string
  title: string
  /** The version to copy; `null` when the class has none yet (the action is then not offered). */
  revisionId: string | null
  versionNumber: number | null
  label?: string
}) {
  const { navigate } = useRouter()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(copyTitle(title))
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const nameId = useId()
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
        const made = await duplicateClass(classId, revisionId, await contentHash(full.raw), name.trim())
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
        setOpen(next)
        if (next) {
          setName(copyTitle(title))
          setError(null)
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="secondary" aria-label={`${label} ${title}`}>
          {label}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Duplicate this class</DialogTitle>
        <DialogDescription>
          {versionNumber !== null ? `Version ${String(versionNumber)} becomes` : 'This version becomes'} version 1 of a
          new class. It is not saved to MOWL and has no approved songs or conversation yet. It counts as one class this
          month.
        </DialogDescription>
        <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Duplicate this class">
          <div className="field">
            <label htmlFor={nameId}>Name of the copy</label>
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
              {sending ? 'Duplicating…' : 'Duplicate'}
            </Button>
          </p>
        </form>
      </DialogContent>
    </Dialog>
  )
}
