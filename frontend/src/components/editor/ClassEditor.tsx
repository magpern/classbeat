import { useMemo, useState } from 'react'
import type { ClassEditor as Editor } from '../../hooks/editor/useClassEditor'
import type { EditorData } from '../../hooks/editor/loadEditor'
import { useT } from '../../i18n/I18n'
import { dragOp } from '../../lib/editor/blockActions'
import { totalSec } from '../../lib/editor/blocks'
import { audioKey } from '../../lib/editor/audioKey'
import { studioClips } from '../../lib/editor/clips'
import { marksByBlock } from '../../lib/editor/overlayMarks'
import { zoneName } from '../../lib/profile'
import { clock } from '../../lib/workout'
import { hasCursor } from '../../preview/transport'
import { mapStudio } from '../../preview/timeModel'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'
import { BlockInspector } from './BlockInspector'
import { CutPins } from './CutPins'
import { EditOutcome } from './EditOutcome'
import { EditorTimeline } from './EditorTimeline'
import { EditorToolbar } from './EditorToolbar'
import { SpotifyAudition } from './spotify/SpotifyAudition'
import { PreviewControls } from './PreviewControls'
import { ProvenanceCard } from './ProvenanceCard'
import { useEditorPlayback } from './useEditorPlayback'

/**
 * The editor of a studio draft or a Spotify draft (ADR 0042, #1424; a Spotify draft has no cut pins, anchors or rendered audio, its music is heard in the audition panel): the zone blocks on the class profile with the songs
 * under them, one block's controls, the cut pins and undo, and the audio preview (Play and Pause).
 * The cursor is drawn only from what the transport observed. Where the server has no preview the
 * transport is the silent clock cursor.
 */
export function ClassEditor({ data, editor }: { data: EditorData; editor: Editor }) {
  const t = useT()
  const { blocks, overlay, timeline, spotify } = data
  const [selected, setSelected] = useState<number | null>(null)
  const [focusPiece, setFocusPiece] = useState<number | null>(null)
  const marks = useMemo(() => marksByBlock(blocks, overlay.records), [blocks, overlay.records])
  const clips = useMemo(() => studioClips(timeline), [timeline])
  const workoutMs = totalSec(blocks) * 1000
  const totalMs = Math.max(workoutMs, ...clips.map((c) => c.endMs))
  const mixEndMs = clips.length > 0 ? Math.max(...clips.map((c) => c.endMs)) : workoutMs
  const key = useMemo(() => audioKey(timeline), [timeline])
  const cursor = useEditorPlayback({ classId: data.summary.id, workoutMs, mixEndMs, audioKey: key, silentOnly: spotify })
  const cursorMs = hasCursor(cursor.state) ? (cursor.state.transportMs ?? null) : null
  const index = selected !== null && selected < blocks.length ? selected : null
  const audition = (classMs: number) => {
    const heard = mapStudio(clips, classMs)
    const piece = heard[heard.length - 1] // inside a crossfade the incoming song is the default
    cursor.audition(piece?.piece ?? 0, piece?.sourceMs ?? 0, classMs)
  }
  const busy = editor.busy
  const unapplied = overlay.unapplied.length + overlay.orphaned.length
  return (
    <div className="flex flex-col gap-5" data-testid="class-editor">
      {cursor.rendered || spotify ? null : <p className="m-0 text-muted">{t('editor.noAudio')}</p>}
      <EditorToolbar
        key={workoutMs}
        canUndo={overlay.undo !== null}
        canRedo={overlay.redo !== null}
        hasOverlay={overlay.records.length > 0}
        totalSec={workoutMs / 1000}
        busy={busy}
        onUndo={editor.undo}
        onRedo={editor.redo}
        onEdit={editor.apply}
      />
      <EditOutcome classId={data.summary.id} outcome={editor.outcome} busy={busy} onReapply={editor.reapply} onDismiss={editor.dismiss} />
      <EditorTimeline
        blocks={blocks}
        totalMs={totalMs}
        marks={marks}
        clips={clips}
        selected={index}
        onSelect={setSelected}
        onDrag={(at, delta) => {
          const op = dragOp(blocks, at, delta)
          if (op !== null) editor.apply(op)
        }}
        cursorMs={cursorMs}
        onAudition={audition}
        focusPiece={focusPiece}
        onFocusPiece={setFocusPiece}
        disabled={busy}
      />
      {cursor.rendered ? (
        <PreviewControls
          state={cursor.state}
          onPlay={() => {
            audition(cursorMs !== null && cursorMs < mixEndMs ? cursorMs : 0)
          }}
          onPause={cursor.pause}
        />
      ) : null}
      <div className="flex flex-wrap items-center gap-3" data-testid="cursor-status">
        <p className="m-0 text-[0.9375rem] text-muted" aria-live="off">
          {cursorMs === null ? t('editor.cursor.none') : t('editor.cursor.at', { time: clock(cursorMs / 1000) })}
        </p>
        {!cursor.rendered && cursor.state.status === 'playing' ? (
          <Button variant="ghost" onClick={cursor.pause}>
            {t('editor.cursor.stop')}
          </Button>
        ) : null}
      </div>
      <ProvenanceCard clips={clips} piece={focusPiece} cursorMs={cursorMs} />
      {unapplied > 0 ? <Notice>{t('editor.unapplied', { count: unapplied })}</Notice> : null}
      <ol className="m-0 flex list-none flex-col gap-1 p-0" aria-label={t('editor.list.label')} data-testid="block-list">
        {blocks.map((block) => (
          <li key={block.index}>
            <button
              type="button"
              aria-pressed={index === block.index}
              onClick={() => {
                setSelected(block.index)
              }}
              className="flex min-h-11 w-full cursor-pointer flex-wrap items-center gap-2 rounded-md border border-line bg-raised px-3 py-1 text-left text-[0.9375rem] text-text aria-pressed:border-brand"
            >
              <span className="font-medium">
                {t('editor.list.item', {
                  n: block.index + 1,
                  name: block.segmentName,
                  from: clock(block.startMs / 1000),
                  to: clock(block.endMs / 1000),
                  zone: block.zone,
                  zoneName: zoneName(block.zone),
                })}
              </span>
              {marks[block.index]?.lock !== null ? <Chip tone="warn">{t('editor.list.locked')}</Chip> : null}
              {Object.keys(marks[block.index]?.overrides ?? {}).length > 0 ? <Chip>{t('editor.list.edited')}</Chip> : null}
            </button>
          </li>
        ))}
      </ol>
      {index === null ? (
        <p className="m-0 text-muted">{t('editor.block.select')}</p>
      ) : (
        <BlockInspector blocks={blocks} marks={marks} timeline={timeline} index={index} music={!spotify} busy={busy} onEdit={editor.apply} onAudition={audition} />
      )}
      {spotify ? null : <CutPins timeline={timeline} records={overlay.records} busy={busy} onEdit={editor.apply} />}
      {spotify ? <SpotifyAudition key={`${data.summary.id}-${data.revisionId}`} classId={data.summary.id} /> : null}
    </div>
  )
}
