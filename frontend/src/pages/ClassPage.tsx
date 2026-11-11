import { useState } from 'react'
import { isSongKind } from '../api/songs'
import { DeleteClass } from '../components/classes/DeleteClass'
import { ChatPanel } from '../components/classes/ChatPanel'
import { ChangeMusic } from '../components/classes/ChangeMusic'
import { PlannedSongs } from '../components/classes/PlannedSongs'
import { DesignInPlace } from '../components/classes/DesignInPlace'
import { UpgradeLink } from '../components/plans/UpgradeLink'
import { HandoffStatus } from '../components/classes/HandoffStatus'
import { HistoryPanel } from '../components/classes/HistoryPanel'
import { RequestBlock } from '../components/classes/RequestBlock'
import { Workspace, type WorkspaceTab } from '../components/classes/Workspace'
import { CHANGE_SONGS_ID, ChangeSongs } from '../components/songs/ChangeSongs'
import { useGoToPanel } from '../hooks/useGoToPanel'
import { useUseRevision } from '../hooks/useUseRevision'
import { useApprovedSongs } from '../hooks/useApprovedSongs'
import { useHandoff } from '../hooks/useHandoff'
import { NotCurrent, PlaylistLine } from '../components/classes/ClassNotes'
import { JobNotice } from '../components/classes/JobNotice'
import { ClassHeader } from '../components/classes/ClassHeader'
import { UncertainWrite } from '../components/classes/UncertainWrite'
import { useSaveFlow } from '../components/classes/useSaveFlow'
import { BeatOnePanel } from '../components/classes/BeatOnePanel'
import { RevisionView } from '../components/classes/RevisionView'
import { SwedebeatSection } from '../components/swedebeat/SwedebeatSection'
import { Card, Notice } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { useClassData, useRevision, type ClassData } from '../hooks/useClassData'
import { Link } from '../router/Router'
import { useT } from '../i18n/I18n'

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
  const save = useSaveFlow(summary, reload)
  // A chosen version that is gone after a reload falls back to the current one.
  const viewingId =
    chosen !== null && revisions.some((r) => r.id === chosen) ? chosen : summary.currentRevisionId
  const shown = useRevision(classId, viewingId)
  const busy = summary.openJob !== null
  const [deleting, setDeleting] = useState(false)
  // Whether the class's history says Change it works (true until it says otherwise).
  const [canChange, setCanChange] = useState(true)
  // A soundless class has no songs to change (#530).
  const canChangeSongs =
    !summary.soundless && summary.songs.state === 'approved' && summary.songs.revisionId !== null
  const songs = useApprovedSongs(classId, summary)
  const handoff = useHandoff(summary, reload)
  // A design with no version yet is shown in place, not on the job page (MA3), and so is a song
  // job (MA4): its list waits for approval right here.
  const design =
    (summary.currentRevisionId === null && summary.openJob?.kind === 'design_draft') ||
    (summary.openJob !== null && isSongKind(summary.openJob.kind))
      ? summary.openJob
      : null
  const [tab, setTab] = useState<WorkspaceTab>(summary.currentRevisionId === null && design === null ? 'chat' : 'class')
  const [designed, setDesigned] = useState(false)
  const goTo = useGoToPanel(tab, setTab)
  const { use, error: useError, upgrade: useUpgrade, busy: using } = useUseRevision(classId, reload, () => {
    setChosen(null)
  })

  const viewing = revisions.find((r) => r.id === viewingId)
  const answeredBy = messages.find((m) => m.resultRevisionId === viewingId && m.role === 'assistant')
  // The first message of a class made on the start screen is the request itself (no base version).
  const first = messages.at(0)
  const request = first?.role === 'user' && first.baseRevisionId === null ? first : null
  // A revise version keeps the reading of its change request, not of the request above: only a
  // designed version's reading belongs under it (a revised-only view shows none, by design).
  const designedRevision = shown.status === 'ready' && shown.revision.origin === 'design_draft'
  const items = (designedRevision ? shown.revision.interpretation : null) ?? handoff.items
  const job = summary.openJob
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
      <Workspace
        tab={tab}
        onTab={setTab}
        chat={
          <>
            <RequestBlock request={request} items={items} />
            <ChatPanel
              summary={summary}
              revisions={revisions}
              messages={messages}
              canChange={canChange}
              requestJobId={request?.jobId ?? null}
              onReload={reload}
              onView={setChosen}
              onChangeSongs={() => {
                goTo(CHANGE_SONGS_ID)
              }}
            />
          </>
        }
        work={
          <>
            <p role="status" className="sr-only">
              {designed && summary.currentRevisionId !== null ? t('ws.ready') : ''}
            </p>
            {design !== null ? (
              <DesignInPlace
                key={design.id}
                jobId={design.id}
                onChanged={reload}
                onCompleted={() => {
                  setDesigned(true)
                  reload()
                }}
              />
            ) : null}
            <HandoffStatus classId={classId} handoff={handoff} />
            {job !== null && design === null && job.kind !== 'revise_draft' && job.kind !== 'interpret_request' ? (
              <JobNotice job={job} />
            ) : null}
            {useError ? <Notice kind="error">{useError}</Notice> : null}
            {useUpgrade ? <UpgradeLink /> : null}
            {summary.currentRevisionId === null && design === null ? (
              <Notice>
                {t('class.page.noVersion')}{' '}
                {job === null ? <Link to="/classes/new">{t('class.list.design')}</Link> : null}
              </Notice>
            ) : null}
            {viewing !== undefined && !viewing.isCurrent ? (
              <NotCurrent
                viewing={viewing.number}
                current={summary.currentRevisionNumber}
                busy={busy}
                working={using}
                onUse={() => {
                  use(viewing)
                }}
                onBack={() => {
                  setChosen(null)
                }}
              />
            ) : null}
            {shown.status === 'loading' ? <p className="m-0">{t('class.page.loadingVersion')}</p> : null}
            {shown.status === 'failed' ? <Notice kind="error">{t('class.page.versionFailed')}</Notice> : null}
            {shown.status === 'ready' ? (
              <RevisionView
                revision={shown.revision}
                musicChanged={!summary.soundless && answeredBy?.change?.musicChanged === true}
                songs={songs}
                classId={classId}
              />
            ) : null}
            {shown.status === 'ready' ? <PlannedSongs segments={shown.revision.segments} songs={songs} /> : null}
            {summary.beatOne.available && viewingId === summary.currentRevisionId ? (
              <BeatOnePanel
                key={summary.beatOne.revisionId ?? 'none'}
                summary={summary}
                busy={busy}
                onReload={reload}
              />
            ) : null}
            {summary.playlist !== null ? <PlaylistLine playlist={summary.playlist} /> : null}
            {summary.sport === 'running' ? null : <SwedebeatSection summary={summary} onReload={reload} />}
            <ChangeMusic
              summary={summary}
              request={request?.body ?? null}
              canChangeSongs={canChangeSongs}
              onChangeSongs={() => {
                goTo(CHANGE_SONGS_ID)
              }}
            />
            <ChangeSongs summary={summary} />
            <div id="versions" className="scroll-mt-4">
              <HistoryPanel
                classId={summary.id}
                revisions={revisions}
                viewingId={viewingId}
                onView={setChosen}
                onUse={use}
                onCanChange={setCanChange}
                busy={using || busy}
              />
            </div>
          </>
        }
      />
    </div>
  )
}
