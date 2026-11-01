import { useCallback, useEffect, useState } from 'react'
import { classReasonText, isStale } from '../api/classMessages'
import { getRevision, pickCurrent, type RevisionSummary } from '../api/classes'
import { getSongList, type Song } from '../api/songs'
import { classStatus } from '../lib/classCards'
import { Chip } from '../components/ui/chip'
import { Button } from '../components/ui/button'
import { DeleteClass } from '../components/classes/DeleteClass'
import { SavePanel } from '../components/classes/SavePanel'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../components/ui/dropdown-menu'
import { Ellipsis } from 'lucide-react'
import { RevisionView } from '../components/classes/RevisionView'
import { SideColumn } from '../components/classes/SideColumn'
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
  const [deleting, setDeleting] = useState(false)
  const canChangeSongs = summary.songs.state === 'approved' && summary.songs.revisionId !== null
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
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <p className="m-0">
        <Link to="/classes">My classes</Link>
      </p>
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="m-0 font-display text-3xl font-extrabold">{summary.title}</h1>
        <Chip tone={classStatus(summary).tone}>{classStatus(summary).label}</Chip>
        <div className="ml-auto flex items-center gap-2">
          {canChangeSongs ? (
            <Button
              variant="secondary"
              onClick={() => {
                goTo('change-songs')
              }}
            >
              Change songs
            </Button>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="secondary" aria-label="More actions">
                <Ellipsis aria-hidden="true" size={18} />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              // Delete class moves focus to its confirmation card; the menu must not take it back.
              onCloseAutoFocus={(event) => {
                if (deleting) event.preventDefault()
              }}
            >
              <DropdownMenuItem
                onSelect={() => {
                  goTo('versions')
                }}
              >
                Versions
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  setDeleting(true)
                }}
              >
                Delete class
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
      <SavePanel summary={summary} onReload={reload} />
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
            ? 'Songs are waiting for your approval. '
            : summary.openJob.kind === 'design_draft'
              ? 'This class is being designed. '
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

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex min-w-0 flex-col gap-3">
          {shown.status === 'loading' ? <p className="m-0">Loading the version</p> : null}
          {shown.status === 'failed' ? (
            <Notice kind="error">This version could not be loaded. Reload the page to try again.</Notice>
          ) : null}
          {shown.status === 'ready' ? (
            <RevisionView
              revision={shown.revision}
              musicChanged={answeredBy?.change?.musicChanged === true}
              songs={songs}
              classId={classId}
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
