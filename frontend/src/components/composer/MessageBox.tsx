import { ArrowRight, Plus } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useT } from '../../i18n/I18n'
import { Notice } from '../forms'

/**
 * The message box of the chat (ADR 0038 section 10.2): the text, an add-music button that starts a
 * sentence about a song, and Send. A refused message's text comes back through `restore`, once per
 * message, and only into an empty box. On a phone the box is fixed above the bottom bar.
 */
export function MessageBox({
  onSend,
  busy,
  blocked,
  error,
  restore,
}: {
  onSend: (text: string) => Promise<boolean>
  busy: boolean
  /** A sentence that says why nothing can be sent now, or null. */
  blocked: string | null
  error: string | null
  /** The text of a message that was refused and has not been put back yet. */
  restore: { id: string; text: string } | null
}) {
  const t = useT()
  const [text, setText] = useState('')
  const field = useRef<HTMLTextAreaElement>(null)
  const restored = useRef<string | null>(null)
  useEffect(() => {
    if (restore === null || restored.current === restore.id) return
    restored.current = restore.id
    // The text comes back only into an empty box: what the user is typing is never overwritten.
    setText((now) => (now.trim() === '' ? restore.text : now))
  }, [restore])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (blocked !== null || busy) return
    void onSend(text).then((sent) => {
      if (sent) setText('')
    })
  }
  const addMusic = () => {
    const phrase = t('cw.chat.addMusic.phrase')
    setText((now) => (now.includes(phrase) ? now : now === '' ? phrase : `${now.trimEnd()} ${phrase}`))
    field.current?.focus()
  }
  return (
    <form
      onSubmit={submit}
      noValidate
      aria-label={t('cw.chat.form')}
      data-testid="message-box"
      className="flex flex-col gap-2 border-line bg-surface max-lg:fixed max-lg:inset-x-0 max-lg:bottom-[3.4rem] max-lg:z-20 max-lg:border-t max-lg:px-4 max-lg:py-2 lg:border-t lg:pt-4"
    >
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {blocked !== null ? <p className="m-0 text-sm text-muted">{blocked}</p> : null}
      <div className="flex items-end gap-2">
        <label htmlFor="cw-message" className="sr-only">
          {t('cw.chat.label')}
        </label>
        <textarea
          id="cw-message"
          ref={field}
          rows={2}
          value={text}
          placeholder={t('cw.chat.placeholder')}
          onChange={(event) => {
            setText(event.target.value)
          }}
          className="min-h-[3.25rem] min-w-0 flex-1 resize-none rounded-xl font-[inherit] border border-line bg-raised px-3 py-3 text-[0.9375rem] text-text"
        />
        <button
          type="button"
          aria-label={t('cw.chat.addMusic')}
          onClick={addMusic}
          className="inline-flex size-12 shrink-0 cursor-pointer items-center justify-center rounded-full border border-line bg-surface text-brand"
        >
          <Plus aria-hidden="true" size={18} strokeWidth={2.2} />
        </button>
        <button
          type="submit"
          aria-label={t('cw.chat.send')}
          disabled={busy || blocked !== null}
          className="inline-flex size-12 shrink-0 cursor-pointer items-center justify-center rounded-full border-0 bg-brand text-on-brand disabled:cursor-not-allowed disabled:opacity-50"
        >
          <ArrowRight aria-hidden="true" size={18} strokeWidth={2.2} />
        </button>
      </div>
    </form>
  )
}
