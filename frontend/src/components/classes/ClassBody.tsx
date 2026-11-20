import { lazy, Suspense, useState } from 'react'
import type { Deleted } from '../../api/classes'
import { isSongKind } from '../../api/songs'
import { DeleteClass } from './DeleteClass'
import { ChatPanel } from './ChatPanel'
import { StudioPanel } from '../studio/StudioPanel'
import { useClassKind } from '../studio/useClassKind'
import { useStudioTimeline } from '../studio/useStudioTimeline'
import { ChangeMusic } from './ChangeMusic'
import { PlannedSongs } from './PlannedSongs'
import { DesignInPlace } from './DesignInPlace'
import { UpgradeLink } from '../plans/UpgradeLink'
import { HandoffStatus } from './HandoffStatus'
import { HistoryPanel } from './HistoryPanel'
import { RequestBlock } from './RequestBlock'
import { readingOf, requestOf } from '../../lib/requestReading'
import { Workspace, type WorkspaceTab } from './Workspace'
import { CHANGE_SONGS_ID, ChangeSongs } from '../songs/ChangeSongs'
import { useGoToPanel } from '../../hooks/useGoToPanel'
import { useUseRevision } from '../../hooks/useUseRevision'
import { useApprovedSongs } from '../../hooks/useApprovedSongs'
import { useHandoff } from '../../hooks/useHandoff'
import { NotCurrent, PlaylistLine } from './ClassNotes'
import { JobNotice } from './JobNotice'
import { ClassHeader } from './ClassHeader'
import { UncertainWrite } from './UncertainWrite'
import { useSaveFlow } from './useSaveFlow'
import { useSendToPlayer } from './useSendToPlayer'
import { sendOf } from '../player/operations'
import { BeatOnePanel } from './BeatOnePanel'
import { RevisionView } from './RevisionView'
import { SwedebeatSection } from '../swedebeat/SwedebeatSection'
import { Notice } from '../forms'
import { useRevision, type ClassData } from '../../hooks/useClassData'
import { Link } from '../../router/Router'
import { useT } from '../../i18n/I18n'
import { versionLine as versionLineOf } from './versionLine'

// The undecided player sends load only for a user who has the player (`player.offered`).
const UncertainPlayer = lazy(() =>
  import('../player/UncertainPlayer').then((m) => ({ default: m.UncertainPlayer })),
)

export function ClassBody({
  classId,
  data,
  reload,
  onDeleted,
}: {
  classId: string
  data: ClassData
  reload: () => void
  onDeleted: (deleted: Deleted) => void
}) {
  const t = useT()
  const { summary, revisions, messages } = data
  const [chosen, setChosen] = useState<string | null>(null)
  const save = useSaveFlow(summary, reload)
  const player = useSendToPlayer(summary, reload)
  // A chosen version that is gone after a reload falls back to the current one.
  const viewingId =
    chosen !== null && revisions.some((r) => r.id === chosen) ? chosen : summary.currentRevisionId
  const shown = useRevision(classId, viewingId)
  const busy = summary.openJob !== null
  const [deleting, setDeleting] = useState(false)
  // Whether the class's history says Change it works (true until it says otherwise).
  const [canChange, setCanChange] = useState(true)
  // A studio class is found by its timeline; the summary's `studio` flag keeps the call off every
  // other class (#1173).
  const studioView = useStudioTimeline(classId, summary.currentRevisionId, summary.openJob === null, summary.studio)
  // A studio class's songs are the studio's own; the legacy song panels do not apply (#1119).
  const kind = useClassKind(summary, studioView)
  const studio = kind === 'studio'
  // The legacy panels wait until the kind is known (a studio class has no legacy song list).
  const legacySongs = kind === 'other'
  const songs = useApprovedSongs(classId, summary, legacySongs)
  // A soundless class has no songs to change (#530).
  const canChangeSongs =
    legacySongs &&
    !summary.soundless && summary.songs.state === 'approved' && summary.songs.revisionId !== null
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
  const request = requestOf(messages)
  const items = readingOf(shown.status === 'ready' ? shown.revision : null, handoff.items)
  const job = summary.openJob
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <p className="m-0">
        <Link to="/classes">{t('class.list.title')}</Link>
      </p>
      <ClassHeader
        summary={summary}
        flow={save}
        player={player}
        viewingId={viewingId}
        versionNumber={viewing?.number ?? null}
        versionLine={shown.status === 'ready' ? versionLineOf(t, shown.revision) : null}
        canChangeSongs={canChangeSongs}
        deleting={deleting}
        onGoTo={goTo}
        onDelete={() => {
          setDeleting(true)
        }}
      />
      <UncertainWrite classId={summary.id} version={save.version} onDecided={save.decided} onChanged={reload} />
      {player.offered ? (
        <Suspense fallback={null}>
          <UncertainPlayer
            keep={sendOf(summary.id)}
            version={player.version}
            onDecided={player.onDecided}
            onChanged={reload}
          />
        </Suspense>
      ) : null}
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
            <RequestBlock request={request} items={items} extras={handoff.extras} />
            <ChatPanel
              summary={summary}
              revisions={revisions}
              messages={messages}
              canChange={canChange}
              studio={studio}
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
                pageTitle={summary.title}
                musicChanged={!summary.soundless && answeredBy?.change?.musicChanged === true}
                songs={songs}
                classId={classId}
              />
            ) : null}
            <StudioPanel summary={summary} view={studioView} studioList={studio} />
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
            {legacySongs ? <ChangeSongs summary={summary} /> : null}
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
