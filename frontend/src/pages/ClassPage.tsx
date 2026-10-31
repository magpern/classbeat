import { useCallback, useState } from 'react'
import { classReasonText, isStale } from '../api/classMessages'
import { getRevision, pickCurrent, type RevisionSummary } from '../api/classes'
import { ChatPanel } from '../components/classes/ChatPanel'
import { DeleteClass } from '../components/classes/DeleteClass'
import { SavePanel } from '../components/classes/SavePanel'
import { RevisionView } from '../components/classes/RevisionView'
import { RevisionsPanel } from '../components/classes/RevisionsPanel'
import { ChangeSongs } from '../components/songs/ChangeSongs'
import { Card, Notice, useAction } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { useClassData, useRevision, type ClassData } from '../hooks/useClassData'
import { contentHash } from '../lib/canonical'
import { Link } from '../router/Router'

export function ClassPage({ classId }: { classId: string }) {
  return (
    <RequireAuth>
      <ClassView classId={classId} />
    </RequireAuth>
  )
}

function ClassView({ classId }: { classId: string }) {
  const { state, reload } = useClassData(classId)
  const [deleted, setDeleted] = useState<number | null>(null)
  if (deleted !== null) {
    return (
      <Card title="Class deleted">
        <p>The class, its versions and your conversation are gone from classbeat.</p>
        {deleted > 0 ? (
          <p>
            The {deleted} workout{deleted === 1 ? '' : 's'} classbeat saved to your MOWL account
            stay{deleted === 1 ? 's' : ''} there; delete {deleted === 1 ? 'it' : 'them'} in MOWL.
          </p>
        ) : null}
        <p>
          <Link className="button" to="/classes">
            My classes
          </Link>
        </p>
      </Card>
    )
  }
  if (state.status === 'loading') {
    return (
      <Card title="One moment">
        <p>Loading your class</p>
      </Card>
    )
  }
  if (state.status === 'not_found') {
    return (
      <Card title="Class not found">
        <p>This class does not exist, or it is not yours.</p>
        <p>
          <Link to="/classes">My classes</Link>
        </p>
      </Card>
    )
  }
  if (state.status === 'failed') {
    return (
      <Card title="The class could not be loaded">
        <Notice kind="error">The server could not be reached or did not answer as expected.</Notice>
        <p>
          <button type="button" className="button" onClick={reload}>
            Try again
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
  const { summary, revisions, messages } = data
  const [chosen, setChosen] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const pick = useAction()
  // A chosen version that is gone after a reload falls back to the current one.
  const viewingId =
    chosen !== null && revisions.some((r) => r.id === chosen) ? chosen : summary.currentRevisionId
  const shown = useRevision(classId, viewingId)
  const busy = summary.openJob !== null

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
          return classReasonText(error)
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
    <div className="class-page">
      <p>
        <Link to="/classes">My classes</Link>
      </p>
      <h1>{summary.title}</h1>
      {summary.openJob !== null && summary.openJob.kind === 'design_draft' ? (
        <Notice>
          This class is being designed.{' '}
          <Link to={`/jobs/${summary.openJob.id}`}>See how it is going</Link>
        </Notice>
      ) : null}
      {summary.openJob !== null && summary.openJob.kind !== 'design_draft' && summary.openJob.kind !== 'revise_draft' ? (
        <Notice>
          {summary.openJob.state === 'waiting_for_approval'
            ? 'Songs are waiting for your approval. '
            : 'The songs of this class are being worked on. '}
          <Link to={`/jobs/${summary.openJob.id}`}>
            {summary.openJob.state === 'waiting_for_approval' ? 'Review the songs' : 'See how it is going'}
          </Link>
        </Notice>
      ) : null}
      {summary.playlist !== null ? (
        <p data-testid="playlist">
          Your Spotify playlist: <strong>{summary.playlist.name}</strong>
          {summary.playlist.link !== null ? (
            <>
              {' '}
              <a href={summary.playlist.link} target="_blank" rel="noopener noreferrer">
                Open in Spotify
              </a>
            </>
          ) : null}
        </p>
      ) : null}
      {notice ? <Notice kind="error">{notice}</Notice> : null}
      {pick.error ? <Notice kind="error">{pick.error}</Notice> : null}

      {summary.currentRevisionId === null ? (
        <Notice>
          This class has no version yet.{' '}
          {summary.openJob === null ? (
            <Link to="/classes/new">Design a class</Link>
          ) : null}
        </Notice>
      ) : null}

      {viewing !== undefined && !viewing.isCurrent ? (
        <div className="notice" data-kind="info" role="status" data-testid="not-current">
          <p>
            You are looking at version {viewing.number}. The class uses version{' '}
            {summary.currentRevisionNumber ?? '?'}, and changes start from that one.
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
              Use this version
            </button>
            <button
              type="button"
              className="button button--secondary"
              onClick={() => {
                setChosen(null)
              }}
            >
              Back to version {summary.currentRevisionNumber ?? '?'}
            </button>
          </p>
          {busy ? <p className="muted">Wait for the running task to finish first.</p> : null}
        </div>
      ) : null}

      {shown.status === 'loading' ? <p>Loading the version</p> : null}
      {shown.status === 'failed' ? (
        <Notice kind="error">This version could not be loaded. Reload the page to try again.</Notice>
      ) : null}
      {shown.status === 'ready' ? (
        <RevisionView revision={shown.revision} musicChanged={answeredBy?.change?.musicChanged === true} />
      ) : null}

      <ChangeSongs summary={summary} />
      <RevisionsPanel
        revisions={revisions}
        viewingId={viewingId}
        onView={setChosen}
        onUse={use}
        busy={pick.busy || busy}
      />
      <SavePanel summary={summary} onReload={reload} />
      <ChatPanel
        summary={summary}
        revisions={revisions}
        messages={messages}
        onReload={reload}
        onView={setChosen}
      />
      <DeleteClass summary={summary} onDeleted={onDeleted} onReload={reload} />
    </div>
  )
}
