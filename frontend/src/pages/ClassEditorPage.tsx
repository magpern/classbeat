import { ChevronLeft } from 'lucide-react'
import { ClassEditor } from '../components/editor/ClassEditor'
import { EditorReadOnly } from '../components/editor/EditorReadOnly'
import { Notice } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { Button } from '../components/ui/button'
import { useClassEditor } from '../hooks/editor/useClassEditor'
import { useT } from '../i18n/I18n'
import { Link } from '../router/Router'

/**
 * The Class Preview and Editor (ADR 0042): a studio draft's blocks, songs and cut pins, edited with
 * the pointer or the keyboard. A draft that cannot be edited (a Spotify draft, a draft the studio
 * revise changed, one whose music changed) opens read-only with the reason, never a broken editor.
 */
export function ClassEditorPage({ classId }: { classId: string }) {
  return (
    <RequireAuth>
      <EditorView classId={classId} />
    </RequireAuth>
  )
}

function EditorView({ classId }: { classId: string }) {
  const t = useT()
  const editor = useClassEditor(classId)
  const { state } = editor
  const title = state.status === 'done' && state.kind !== 'not_found' ? (state.kind === 'ready' ? state.data.summary.title : state.summary.title) : null
  return (
    <section className="flex flex-col gap-4" data-testid="editor-page">
      <p className="m-0">
        <Link to={`/classes/${classId}`} className="inline-flex items-center gap-1">
          <ChevronLeft aria-hidden="true" className="size-4" />
          {t('editor.back')}
        </Link>
      </p>
      <h1 className="m-0 font-display text-2xl font-bold">{title === null ? t('editor.title') : t('editor.titleFor', { title })}</h1>
      {state.status === 'loading' ? <p className="m-0">{t('common.loading')}</p> : null}
      {state.status === 'failed' ? (
        <div className="flex flex-col gap-3">
          <Notice kind="error">{t('editor.loadFailed')}</Notice>
          <div>
            <Button onClick={editor.reload}>{t('editor.retry')}</Button>
          </div>
        </div>
      ) : null}
      {state.status === 'done' && state.kind === 'not_found' ? <Notice kind="error">{t('editor.notFound')}</Notice> : null}
      {state.status === 'done' && state.kind === 'readonly' ? <EditorReadOnly reason={state.reason} summary={state.summary} onReload={editor.reload} /> : null}
      {state.status === 'done' && state.kind === 'ready' ? <ClassEditor data={state.data} editor={editor} /> : null}
    </section>
  )
}
