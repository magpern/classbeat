import { useEffect, useRef, useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { getSongList } from '../../api/songs'
import {
  approveSwedebeatMatch,
  createCatalogueReplaceJob,
  needsReconnect,
  swedebeatError,
  type MatchSong,
  type SwedebeatMatch,
} from '../../api/swedebeatMatch'
import { currentConnectionIds, digest, isNotConnected, minutes } from '../../lib/songs'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { Chip } from '../ui/chip'
import { ReconnectPrompt } from './ReconnectPrompt'

const STATUS: Record<MatchSong['status'], { label: string; tone: 'ok' | 'warn' | 'bad' }> = {
  found: { label: 'Found in Swedebeat', tone: 'ok' },
  unsure: { label: 'Unsure match', tone: 'warn' },
  missing: { label: 'Not in Swedebeat', tone: 'bad' },
}

/**
 * The Swedebeat match of the class's songs (ADR 0016 section 6): found, unsure and missing songs,
 * the mix length against the plan, and the user's decisions: keep as is and approve, or replace
 * the missing and unsure songs. Nothing is written to Swedebeat from here.
 */
export function MatchCard({
  summary,
  match,
  approved,
  onReload,
}: {
  summary: ClassSummary
  match: SwedebeatMatch
  approved: boolean
  onReload: () => void
}) {
  const [planned, setPlanned] = useState<number | null>(null)
  const [reconnect, setReconnect] = useState(false)
  const approve = useAction()
  const replace = useAction()
  const locked = useRef(false)
  const { navigate } = useRouter()

  const listId = match.parentId
  useEffect(() => {
    if (listId === null) return
    const abort = new AbortController()
    getSongList(summary.id, listId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setPlanned(found.totalSec)
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [summary.id, listId])

  const busy = summary.openJob !== null || approve.busy || replace.busy
  const replaceable = match.unsure + match.missing
  const base = summary.currentRevisionId

  const onApprove = () => {
    void approve
      .run(
        async () => {
          await approveSwedebeatMatch(summary.id, match.id)
          onReload()
        },
        (error) => {
          if (needsReconnect(error)) setReconnect(true)
          return swedebeatError(error)
        },
      )
  }

  const onReplace = () => {
    if (base === null || locked.current) return
    locked.current = true
    const scope = `swedebeat-replace.${summary.id}.${base}.${match.id}.${digest(match.contentHash)}`
    void replace
      .run(
        async () => {
          const ids = await currentConnectionIds()
          const job = await createCatalogueReplaceJob(
            {
              class_id: summary.id,
              base_revision_id: base,
              match_revision_id: match.id,
              mowl_connection_id: ids.mowl,
              spotify_connection_id: ids.spotify,
            },
            actionKey(scope),
          )
          dropActionKey(scope)
          navigate(`/jobs/${job.id}`)
        },
        (error) => {
          if (needsReconnect(error)) setReconnect(true)
          return isNotConnected(error)
            ? 'Spotify, MOWL or Swedebeat is not connected. Connect it on your account page first.'
            : swedebeatError(error)
        },
      )
      .finally(() => {
        locked.current = false
      })
  }

  return (
    <Card
      id="swedebeat-match"
      role="region"
      aria-labelledby="swedebeat-match-title"
      tabIndex={-1}
      className="flex scroll-mt-4 flex-col gap-3"
    >
      <CardTitle id="swedebeat-match-title">Swedebeat match</CardTitle>
      <p className="m-0 text-muted" data-testid="match-totals">
        {match.found} found, {match.unsure} unsure, {match.missing} not in Swedebeat.
      </p>
      <p className="m-0 text-muted" data-testid="match-length">
        The mix would be {minutes(match.swedebeatSec)} long by Swedebeat&rsquo;s versions
        {planned !== null ? `, against ${minutes(planned)} planned.` : '.'}
      </p>
      <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Matched songs">
        {match.songs.map((song) => (
          <li
            key={song.position}
            className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-surface p-3"
            data-testid="match-song"
          >
            <span className="w-8 text-right text-muted">{song.position}</span>
            <div className="min-w-40 flex-1 basis-40">
              <p className="m-0 break-words font-medium text-text">{song.wantedTitle}</p>
              <p className="m-0 break-words text-[0.9375rem] text-muted">{song.wantedArtist}</p>
              {song.swedebeat !== null ? (
                <p className="m-0 break-words text-[0.9375rem] text-muted">
                  Swedebeat has: {song.swedebeat.title}, {song.swedebeat.artist}
                  {song.swedebeat.durationSec !== null ? ` (${minutes(song.swedebeat.durationSec)})` : ''}
                </p>
              ) : null}
            </div>
            <Chip tone={STATUS[song.status].tone}>{STATUS[song.status].label}</Chip>
          </li>
        ))}
      </ol>
      {match.missing > 0 ? (
        <Notice>
          {match.missing === 1 ? 'One song is' : `${String(match.missing)} songs are`} not in Swedebeat. If you
          keep {match.missing === 1 ? 'it' : 'them'}, the mix will lack {match.missing === 1 ? 'it' : 'them'}.
        </Notice>
      ) : null}
      {match.unsure > 0 ? (
        <Notice>
          Check the unsure songs above. Keep them only if Swedebeat&rsquo;s version is the same song.
        </Notice>
      ) : null}
      {summary.openJob !== null ? <p className="m-0 text-muted">Wait for the running task to finish first.</p> : null}
      {reconnect ? <ReconnectPrompt /> : null}
      {approve.error ? <Notice kind="error">{approve.error}</Notice> : null}
      {replace.error ? <Notice kind="error">{replace.error}</Notice> : null}
      {approved ? (
        <p className="m-0">
          <Chip tone="ok">Selection approved</Chip> Nothing was written to Swedebeat.
        </p>
      ) : (
        <div className="flex flex-wrap gap-3">
          <Button disabled={busy} onClick={onApprove}>
            Approve this selection
          </Button>
        </div>
      )}
      {replaceable > 0 ? (
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" disabled={busy || base === null} onClick={onReplace}>
            Replace the missing and unsure songs
          </Button>
        </div>
      ) : null}
    </Card>
  )
}
