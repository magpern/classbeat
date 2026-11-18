import { Repeat } from 'lucide-react'
import type { DraftSegment } from '../../api/draftTypes'
import { kindLabel } from '../../api/classMessages'
import type { Song, SongSource } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { songRows } from '../../lib/composer/songRows'
import { clock } from '../../lib/workout'
import { Button } from '../ui/button'

function sourceText(source: SongSource | null | undefined, t: ReturnType<typeof useT>): string | null {
  if (source === null || source === undefined) return null
  if (source.kind === 'pinned') return t('cw.source.pinned')
  if (source.kind === 'search') return t('cw.source.found')
  return source.name
}

/**
 * The class's segments, each with the songs that start in it (ADR 0038 section 10.2): title,
 * artist and where it came from, and Swap before the approval. Song titles and playlist names are
 * server text, shown as plain text.
 */
export function SegmentList({
  segments,
  songs,
  hasList,
  canSwap,
  swapping,
  onSwap,
}: {
  segments: readonly DraftSegment[]
  songs: readonly Song[] | null
  /** A song list is shown: a segment without a starting song then plays on the one before. */
  hasList: boolean
  canSwap: boolean
  swapping: boolean
  onSwap: (song: Song) => void
}) {
  const t = useT()
  const rows = songRows(segments, songs)
  return (
    <ol
      aria-label={t('cw.segments')}
      data-testid="segment-list"
      className="m-0 flex list-none flex-col rounded-lg border border-line bg-surface px-4 py-1"
    >
      {rows.map((row) => (
        <li key={row.index} className="flex flex-col gap-1 border-b border-line py-3 last:border-b-0">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <span className="w-12 tabular-nums text-muted">{clock(row.startSec)}</span>
            <span className="min-w-0 break-words font-semibold">{row.name}</span>
            <span className="text-sm text-muted">{kindLabel(row.kind)}</span>
          </div>
          {row.songs.length === 0 ? (
            <p className="m-0 pl-15 text-[0.9375rem] text-muted">{t(hasList ? 'cw.row.continues' : 'cw.row.picks')}</p>
          ) : (
            row.songs.map((song) => {
              const source = sourceText(song.source, t)
              return (
                <div key={song.position} className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-15">
                  <p className="m-0 flex min-w-0 flex-1 basis-56 flex-col text-[0.9375rem]">
                    <span className="break-words">
                      {song.title} <span className="text-muted">· {song.artist}</span>
                    </span>
                    {source !== null ? (
                      <span className={`break-words text-sm ${song.source?.kind === 'pinned' ? 'text-brand' : 'text-muted'}`}>
                        {source}
                      </span>
                    ) : null}
                  </p>
                  {canSwap ? (
                    <Button
                      variant="secondary"
                      className="min-h-11 px-3 text-[0.8125rem]"
                      aria-label={t('cw.swap.label', { title: song.title })}
                      disabled={swapping}
                      onClick={() => {
                        onSwap(song)
                      }}
                    >
                      <Repeat aria-hidden="true" size={14} />
                      {t('cw.swap')}
                    </Button>
                  ) : null}
                </div>
              )
            })
          )}
        </li>
      ))}
    </ol>
  )
}
