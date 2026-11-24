import { lazy, Suspense, useState } from 'react'
import { approveAndSave } from '../../api/approveAndSave'
import type { Deleted } from '../../api/classes'
import { fillSongs } from '../../api/fillSongs'
import { pendingOf } from '../../lib/composer/driver'
import { resumePlaylist } from '../../api/resumePlaylist'
import { approveRefusal, startError } from '../../api/songMessages'
import type { Song } from '../../api/songs'
import { swapSong } from '../../api/swapSong'
import { useComposerDriver } from '../../hooks/composer/useComposerDriver'
import { usePendingMessages } from '../../hooks/composer/usePendingMessages'
import { useReading } from '../../hooks/composer/useReading'
import { useSendMessage } from '../../hooks/composer/useSendMessage'
import { useSongsOfClass } from '../../hooks/composer/useSongsOfClass'
import { useRevision, type ClassData } from '../../hooks/useClassData'
import { useHandoff } from '../../hooks/useHandoff'
import { useUseRevision } from '../../hooks/useUseRevision'
import { useT } from '../../i18n/I18n'
import { deriveChip } from '../../lib/composer/chip'
import { recallRequest } from '../../lib/startRequest'
import { requestOf } from '../../lib/requestReading'
import { sendOf } from '../player/operations'
import { Link } from '../../router/Router'
import { DeleteClass } from '../classes/DeleteClass'
import { HandoffStatus } from '../classes/HandoffStatus'
import { NotCurrent } from '../classes/ClassNotes'
import { UncertainWrite } from '../classes/UncertainWrite'
import { useSaveFlow } from '../classes/useSaveFlow'
import { useSendToPlayer } from '../classes/useSendToPlayer'
import { Notice, useAction } from '../forms'
import { ChatColumn } from './ChatColumn'
import { ClassColumn } from './ClassColumn'
import { MoreMenu, MorePanel, type MoreSection } from './MoreMenu'
import { ApproveBar } from './ApproveBar'
import { SaveArea } from './SaveArea'

// The undecided player sends load only for a user who has the player (`player.offered`).
const UncertainPlayer = lazy(() => import('../player/UncertainPlayer').then((m) => ({ default: m.UncertainPlayer })))

type Props = {
  classId: string
  data: ClassData
  reload: () => void
  onDeleted: (deleted: Deleted) => void
  /** The operator has turned the composer off (`composer_flow` false for this user): a notice, no editing. */
  paused?: boolean
}

/**
 * The class page of a composer class (ADR 0038 sections 8 and 10.2): the chat beside the class
 * and the web's one driver. On a phone the class comes first and the message box is fixed at the
 * bottom; from 1024 px the chat is on the left.
 */
export function ComposerWorkspace({ classId, data, reload, onDeleted, paused = false }: Props) {
  const t = useT()
  const { summary, revisions, messages } = data
  const [chosen, setChosen] = useState<string | null>(null)
  const [more, setMore] = useState<MoreSection | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [fromHistory, setCanChange] = useState(true)
  // The class answer says it at load (#1484); the history's value only fills in where it does not.
  const canChange = summary.canChange ?? fromHistory
  const handoff = useHandoff(summary, reload)
  const reading = useReading(classId, summary.currentRevisionId)
  const stored = usePendingMessages(classId, summary)
  const save = useSaveFlow(summary, reload)
  const player = useSendToPlayer(summary, reload)
  const list = useSongsOfClass(classId, summary.songs)
  const currentOrigin = revisions.find((r) => r.id === summary.currentRevisionId)?.origin ?? null
  const driver = useComposerDriver({
    summary,
    currentOrigin,
    messages: stored.messages,
    reload,
    refreshMessages: stored.refresh,
    paused,
  })
  const viewingId = chosen !== null && revisions.some((r) => r.id === chosen) ? chosen : summary.currentRevisionId
  const shown = useRevision(classId, viewingId)
  const viewing = revisions.find((r) => r.id === viewingId)
  const using = useUseRevision(classId, reload, () => {
    setChosen(null)
  })
  const chip = deriveChip({ ...summary, currentOrigin })

  const sender = useSendMessage(classId, stored.refresh)
  const pending = pendingOf(stored.messages ?? [])
  const newest = stored.messages?.at(-1) ?? null
  const failed = newest !== null && newest.status === 'failed' ? newest : null
  const running = summary.openJob !== null && (summary.openJob.state === 'queued' || summary.openJob.state === 'running')
  const request = requestOf(messages)
  const shownMessages = messages.filter((m) => m.seq !== request?.seq && (request === null || m.jobId !== request.jobId))
  const blocked =
    paused
      ? t('cw.paused')
      : summary.currentRevisionId === null
      ? t('cw.chat.noVersion')
      : pending !== null
        ? t('cw.chat.messagePending')
        : !canChange
          ? t('class.chat.unavailable')
          : null

  const ready = chip.state === 'ready' && list !== null && list.status === 'waiting'
  // A waiting list shows its own designed class: that is what the approval covers.
  const segments = ready && list.preview != null ? list.preview.segments : shown.status === 'ready' ? shown.revision.segments : null
  const title = ready && list.preview != null ? list.preview.name : shown.status === 'ready' ? shown.revision.name : summary.title
  const act = useAction()
  const approve = useAction()

  const run = (task: () => Promise<unknown>) =>
    void act.run(task, startError).then((done) => {
      if (done !== undefined) reload()
    })
  const sketchId = summary.currentRevisionId
  const onSwap = (song: Song) => {
    if (list === null) return
    run(() =>
      swapSong({
        classId,
        listRevisionId: list.id,
        listJobId: list.jobId ?? summary.songs.jobId ?? list.id,
        slot: song.position - 1,
      }),
    )
  }
  const onApprove = () => {
    if (list === null) return
    const jobId = list.jobId ?? summary.songs.jobId
    if (jobId === null) return
    void approve.run(() => approveAndSave(jobId, list.id, list.contentHash), (e) => approveRefusal(e).text).then((done) => {
      if (done !== undefined) reload()
    })
  }

  const below = (
    <>
      <HandoffStatus classId={classId} handoff={handoff} />
      {driver.error !== null ? <p className="notice" data-kind="error" role="alert">{driver.error}</p> : null}
      {viewing !== undefined && !viewing.isCurrent ? (
        <NotCurrent
          viewing={viewing.number}
          current={summary.currentRevisionNumber}
          busy={summary.openJob !== null}
          working={using.busy}
          onUse={() => {
            using.use(viewing)
          }}
          onBack={() => {
            setChosen(null)
          }}
        />
      ) : null}
      {using.error !== null ? <p className="notice" data-kind="error" role="alert">{using.error}</p> : null}
      {ready ? (
        <ApproveBar holding={pending !== null} checking={stored.messages === null} checkFailed={stored.failed} onRetryCheck={stored.refresh} busy={approve.busy || running} error={approve.error} onApprove={onApprove} />
      ) : (
        <SaveArea flow={save} chip={chip.state} currentOrigin={currentOrigin} differed={summary.composer.phase2 === 'differed'} />
      )}
      <MorePanel
        section={more}
        summary={summary}
        revisions={revisions}
        viewingId={viewingId}
        busy={summary.openJob !== null}
        player={player}
        onClose={() => {
          setMore(null)
        }}
        onReload={reload}
        onView={(id) => {
          setChosen(id)
          setMore(null)
        }}
        onCanChange={setCanChange}
      />
    </>
  )

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 max-lg:pb-36" data-testid="composer-workspace">
      <p className="m-0">
        <Link to="/classes">{t('class.list.title')}</Link>
      </p>
      {paused ? <Notice>{t('cw.paused')}</Notice> : null}
      <UncertainWrite classId={summary.id} version={save.version} onDecided={save.decided} onChanged={reload} />
      {player.offered ? (
        <Suspense fallback={null}>
          <UncertainPlayer keep={sendOf(summary.id)} version={player.version} onDecided={player.onDecided} onChanged={reload} />
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
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <section aria-label={t('cw.region.class')} className="order-1 min-w-0 lg:order-2" data-testid="cw-class">
          <ClassColumn
            summary={summary}
            title={title}
            chip={chip}
            segments={segments}
            songs={list?.songs ?? null}
            hasList={list !== null}
            attached={recallRequest(classId)?.playlists?.length ?? 0}
            currentOrigin={currentOrigin}
            menu={
              <MoreMenu
                summary={summary}
                player={player}
                current={viewingId === summary.currentRevisionId}
                deleting={deleting}
                onPick={setMore}
                onDelete={() => {
                  setDeleting(true)
                }}
              />
            }
            below={below}
            runningJob={running}
            canSwap={ready && !running && act.busy === false}
            swapping={act.busy}
            onSwap={onSwap}
            onFinished={reload}
            onFindAgain={() => {
              if (sketchId !== null) run(() => fillSongs({ classId, sketchRevisionId: sketchId, retry: true }))
            }}
            onCreateAgain={() => {
              const jobId = list?.jobId ?? summary.songs.jobId
              if (jobId !== null) run(() => resumePlaylist({ classId, listJobId: jobId }))
            }}
            actionError={act.error}
            actionBusy={act.busy}
          />
        </section>
        <section aria-label={t('cw.region.chat')} className="order-2 min-w-0 lg:order-1" data-testid="cw-chat">
          <ChatColumn
            classId={classId}
            request={request}
            messages={shownMessages}
            numbers={new Map(revisions.map((r) => [r.id, r.number]))}
            items={handoff.items ?? reading.items}
            extras={handoff.items !== null ? handoff.extras : reading.extras}
            stored={{ pending, failed }}
            send={{ run: sender.send, busy: sender.busy, error: sender.error }}
            blocked={blocked}
            restore={failed === null ? null : { id: failed.messageId, text: failed.body }}
          />
        </section>
      </div>
    </div>
  )
}
