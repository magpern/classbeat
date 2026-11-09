import { useCallback, useEffect, useState } from 'react'
import { currentReasonText, isStale } from '../api/classMessages'
import { getRevision, pickCurrent, type RevisionSummary } from '../api/classes'
import { getSongList, type Song } from '../api/songs'
import { DeleteClass } from '../components/classes/DeleteClass'
import { ClassHeader } from '../components/classes/ClassHeader'
import { UncertainWrite } from '../components/classes/UncertainWrite'
import { useSaveFlow } from '../components/classes/useSaveFlow'
import { BeatOnePanel } from '../components/classes/BeatOnePanel'
import { RevisionView } from '../components/classes/RevisionView'
import { SwedebeatSection } from '../components/swedebeat/SwedebeatSection'
import { SideColumn } from '../components/classes/SideColumn'
import { Card, Notice, useAction } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { useClassData, useRevision, type ClassData } from '../hooks/useClassData'
import { contentHash } from '../lib/canonical'
import { Link } from '../router/Router'
import { Trans, useT } from '../i18n/I18n'

export function ClassPage({ classId }: { classId: string }) {
  return (
    <RequireAuth>
      <ClassView classId={classId} />
    </RequireAuth>
  )
}

function ClassView({ classId }: { classId: string }) {
  const t = useT()
  const { state, reload } = useClassData(classId)
  const [deleted, setDeleted] = useState<number | null>(null)
  if (deleted !== null) {
    return (
      <Card title={t('class.page.deletedTitle')}>
        <p>{t('class.page.deleted')}</p>
        {deleted > 0 ? <p>{t('class.delete.kept', { count: deleted })}</p> : null}
        <p>
          <Link className="button" to="/classes">
            {t('class.list.title')}
          </Link>
        </p>
      </Card>
    )
  }
  if (state.status === 'loading') {
    return (
      <Card title={t('class.page.loadingTitle')}>
        <p>{t('class.page.loading')}</p>
      </Card>
    )
  }
  if (state.status === 'not_found') {
    return (
      <Card title={t('class.page.notFoundTitle')}>
        <p>{t('class.page.notFound')}</p>
        <p>
          <Link to="/classes">{t('class.list.title')}</Link>
        </p>
      </Card>
    )
  }
  if (state.status === 'failed') {
    return (
      <Card title={t('class.page.failedTitle')}>
        <Notice kind="error">{t('class.page.failed')}</Notice>
        <p>
          <button type="button" className="button" onClick={reload}>
            {t('class.tryAgain')}
          </button>
        </p>
      </Card>
    )
  }
  return <ClassBody classId={classId} data={state.data} reload={reload} onDeleted={setDeleted} />
}

function ClassBody({
  classId,
  data,
  reload,
  onDeleted,
}: {
  classId: string
  data: ClassData
  reload: () => void
  onDeleted: (kept: number) => void
}) {
  const t = useT()
  const { summary, revisions, messages } = data
  const [chosen, setChosen] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const pick = useAction()
  const save = useSaveFlow(summary, reload)
  // A chosen version that is gone after a reload falls back to the current one.
  const viewingId =
    chosen !== null && revisions.some((r) => r.id === chosen) ? chosen : summary.currentRevisionId
  const shown = useRevision(classId, viewingId)
  const busy = summary.openJob !== null
  const [deleting, setDeleting] = useState(false)
  // A soundless class has no songs to change (#530).
  const canChangeSongs =
    !summary.soundless && summary.songs.state === 'approved' && summary.songs.revisionId !== null
  const goTo = (id: string) => {
    const target = document.getElementById(id)
    target?.scrollIntoView?.({ block: 'start' })
    target?.querySelector<HTMLElement>('button, input, textarea, a')?.focus()
  }
  // The approved song list, for the strip under the axis; none (hidden) until there is one.
  const songRevisionId = summary.songs.state === 'approved' ? summary.songs.revisionId : null
  const [loadedSongs, setLoadedSongs] = useState<{ id: string; songs: Song[] } | null>(null)
  useEffect(() => {
    if (songRevisionId === null) return
    const abort = new AbortController()
    getSongList(classId, songRevisionId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setLoadedSongs({ id: songRevisionId, songs: found.songs })
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [classId, songRevisionId])
  const songs = loadedSongs !== null && loadedSongs.id === songRevisionId ? loadedSongs.songs : null

  const reloadWithNotice = useCallback(() => {
    reload()
  }, [reload])

  const use = (revision: RevisionSummary) => {
    setNotice(null)
    void pick
      .run(
        async () => {
          // The hash of the content the server holds for this version: it only goes through if
          // the revision is the one named (ADR 0007 section 2).
          const full = await getRevision(classId, revision.id)
          return pickCurrent(classId, revision.id, await contentHash(full.raw))
        },
        (error) => {
          if (isStale(error)) reloadWithNotice()
          return currentReasonText(error)
        },
      )
      .then((done) => {
        if (done !== undefined) {
          setChosen(null)
          reload()
        }
      })
  }

  const viewing = revisions.find((r) => r.id === viewingId)
  const answeredBy = messages.find((m) => m.resultRevisionId === viewingId && m.role === 'assistant')
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <p className="m-0">
        <Link to="/classes">{t('class.list.title')}</Link>
      </p>
      <ClassHeader
        summary={summary}
        flow={save}
        viewingId={viewingId}
        versionNumber={viewing?.number ?? null}
        canChangeSongs={canChangeSongs}
        deleting={deleting}
        onGoTo={goTo}
        onDelete={() => {
          setDeleting(true)
        }}
      />
      <UncertainWrite classId={summary.id} version={save.version} onDecided={save.decided} onChanged={reload} />
      <DeleteClass
        summary={summary}
        open={deleting}
        onClose={() => {
          setDeleting(false)
        }}
        onDeleted={onDeleted}
        onReload={reload}
      />
      {summary.openJob !== null && summary.openJob.kind !== 'revise_draft' ? (
        <Notice>
          {summary.openJob.state === 'waiting_for_approval'
            ? t('class.page.job.waiting')
            : summary.openJob.kind === 'design_draft'
              ? t('class.page.job.design')
              : summary.openJob.kind === 'refit_draft'
                ? t('class.page.job.refit')
                : t('class.page.job.songs')}{' '}
          <Link to={`/jobs/${summary.openJob.id}`}>
            {summary.openJob.state === 'waiting_for_approval'
              ? t('class.page.job.review')
              : t('class.page.job.progress')}
          </Link>
        </Notice>
      ) : null}
      {summary.playlist !== null ? (
        <p data-testid="playlist">
          <Trans
            id="class.page.playlist"
            slots={{ name: <strong>{summary.playlist.name}</strong> }}
          />
          {summary.playlist.link !== null ? (
            <>
              {' '}
              <a href={summary.playlist.link} target="_blank" rel="noopener noreferrer">
                {t('class.page.openSpotify')}
              </a>
            </>
          ) : null}
        </p>
      ) : null}
      {notice ? <Notice kind="error">{notice}</Notice> : null}
      {summary.sport === 'running' ? null : <SwedebeatSection summary={summary} onReload={reload} />}
      {pick.error ? <Notice kind="error">{pick.error}</Notice> : null}

      {summary.currentRevisionId === null ? (
        <Notice>
          {t('class.page.noVersion')}{' '}
          {summary.openJob === null ? (
            <Link to="/classes/new">{t('class.list.design')}</Link>
          ) : null}
        </Notice>
      ) : null}

      {viewing !== undefined && !viewing.isCurrent ? (
        <div className="notice" data-kind="info" role="status" data-testid="not-current">
          <p>
            {t('class.page.notCurrent', {
              viewing: viewing.number,
              current: summary.currentRevisionNumber ?? '?',
            })}
          </p>
          <p>
            <button
              type="button"
              className="button"
              disabled={pick.busy || busy}
              onClick={() => {
                use(viewing)
              }}
            >
              {t('class.useThisVersion')}
            </button>
            <button
              type="button"
              className="button button--secondary"
              onClick={() => {
                setChosen(null)
              }}
            >
              {t('class.page.back', { number: summary.currentRevisionNumber ?? '?' })}
            </button>
          </p>
          {busy ? <p className="muted">{t('class.waitRunning')}</p> : null}
        </div>
      ) : null}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex min-w-0 flex-col gap-3">
          {shown.status === 'loading' ? <p className="m-0">{t('class.page.loadingVersion')}</p> : null}
          {shown.status === 'failed' ? (
            <Notice kind="error">{t('class.page.versionFailed')}</Notice>
          ) : null}
          {shown.status === 'ready' ? (
            <RevisionView
              revision={shown.revision}
              musicChanged={!summary.soundless && answeredBy?.change?.musicChanged === true}
              songs={songs}
              classId={classId}
            />
          ) : null}
          {summary.beatOne.available && viewingId === summary.currentRevisionId ? (
            <BeatOnePanel
              key={summary.beatOne.revisionId ?? 'none'}
              summary={summary}
              busy={busy}
              onReload={reload}
            />
          ) : null}
        </div>
        <SideColumn
          summary={summary}
          revisions={revisions}
          messages={messages}
          viewingId={viewingId}
          busy={pick.busy || busy}
          onView={setChosen}
          onUse={use}
          reload={reload}
        />
      </div>
    </div>
  )
}
