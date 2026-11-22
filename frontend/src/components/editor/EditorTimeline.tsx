import { Anchor, Lock, Pencil } from 'lucide-react'
import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useT } from '../../i18n/I18n'
import type { EditorBlock } from '../../lib/editor/blocks'
import { actionsFor, dragRange } from '../../lib/editor/blockActions'
import { LEFT, PLOT_W, VIEW_W, pxToDeltaSec, widthOf, xOf } from '../../lib/editor/geometry'
import type { BlockMarks } from '../../lib/editor/overlayMarks'
import { heightShare } from '../../lib/profile'
import { clock } from '../../lib/workout'
import type { StudioClip } from '../../preview/timeModel'
import { zoneFill } from '../classes/zoneClasses'

// The class on the stage, as the profile chart draws it (x is time, bar height is the zone), with
// the song lane under it. Blocks are selected here or in the list under the chart; a boundary is
// dragged with the pointer or moved with the keyboard (arrow keys choose, Enter moves, Escape
// cancels). The cursor is drawn ONLY from the transport's observed state (`cursorMs`).

const PLOT_H = 110
const TOP = 22
const AXIS_Y = TOP + PLOT_H
const LANE_Y = AXIS_Y + 8
const LANE_H = 26
const H = LANE_Y + LANE_H + 8

export interface EditorTimelineProps {
  blocks: EditorBlock[]
  /** The class length in ms the drawing spans: the workout, or the music when it is longer. */
  totalMs: number
  marks: BlockMarks[]
  clips: StudioClip[]
  selected: number | null
  onSelect: (index: number | null) => void
  /** A boundary moved by whole seconds (the page clamps it and sends the edit). */
  onDrag: (index: number, deltaSec: number) => void
  /** The cursor's class time, from the transport's observed state; null: none. */
  cursorMs: number | null
  onAudition: (classMs: number) => void
  focusPiece: number | null
  onFocusPiece: (piece: number | null) => void
  disabled: boolean
}

interface Drag {
  index: number
  deltaSec: number
}

export function EditorTimeline(props: EditorTimelineProps) {
  const { blocks, totalMs, marks, clips, selected, onSelect, onDrag, cursorMs, onAudition, focusPiece, onFocusPiece, disabled } = props
  const t = useT()
  const svg = useRef<SVGSVGElement>(null)
  const start = useRef(0)
  const [drag, setDrag] = useState<Drag | null>(null)
  const totalSec = totalMs / 1000
  const clamp = (index: number, deltaSec: number) => {
    const { earlier, later } = dragRange(blocks, index)
    return Math.max(-earlier, Math.min(later, deltaSec))
  }
  const finish = (commit: boolean) => {
    if (drag !== null && commit && drag.deltaSec !== 0) onDrag(drag.index, drag.deltaSec)
    setDrag(null)
  }
  const keys = (index: number, event: KeyboardEvent) => {
    const step = event.shiftKey ? 5 : 1
    const now = drag?.index === index ? drag.deltaSec : 0
    if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') setDrag({ index, deltaSec: clamp(index, now - step) })
    else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') setDrag({ index, deltaSec: clamp(index, now + step) })
    else if (event.key === 'Enter' || event.key === ' ') finish(true)
    else if (event.key === 'Escape') finish(false)
    else return
    event.preventDefault()
  }
  const pointerDown = (index: number, event: PointerEvent<SVGRectElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    start.current = event.clientX
    setDrag({ index, deltaSec: 0 })
  }
  const pointerMove = (index: number, event: PointerEvent<SVGRectElement>) => {
    if (drag?.index !== index) return
    const width = svg.current?.getBoundingClientRect().width ?? 0
    setDrag({ index, deltaSec: clamp(index, pxToDeltaSec(event.clientX - start.current, width, totalSec)) })
  }
  return (
    <svg
      ref={svg}
      viewBox={`0 0 ${String(VIEW_W)} ${String(H)}`}
      role="group"
      aria-label={t('editor.tl.label')}
      className="block h-auto w-full rounded-lg bg-stage text-stage-text"
      data-testid="editor-timeline"
    >
      {blocks.map((block) => {
        const height = PLOT_H * heightShare(block.zone)
        const x = xOf(totalMs, block.startMs)
        const mark = marks[block.index]
        const w = Math.max(widthOf(totalMs, block.endMs - block.startMs) - 1, 1)
        return (
          <g key={block.index}>
            <rect
              data-block={block.index}
              data-zone={block.zone}
              x={x}
              y={AXIS_Y - height}
              width={w}
              height={height}
              className={`${zoneFill(block.zone)} ${selected === block.index ? 'stroke-stage-text' : 'stroke-none'} cursor-pointer`}
              strokeWidth={2}
              onClick={() => {
                onSelect(block.index)
              }}
            />
            <g className="stroke-stage-text" fill="none" aria-hidden="true">
              {mark?.lock !== null && mark?.lock !== undefined ? <Lock x={x + 2} y={4} width={14} height={14} data-testid="mark-lock" /> : null}
              {mark?.anchors.start !== undefined || mark?.anchors.end !== undefined ? <Anchor x={x + 17} y={4} width={14} height={14} data-testid="mark-anchor" /> : null}
              {Object.keys(mark?.overrides ?? {}).length > 0 ? <Pencil x={x + 32} y={4} width={14} height={14} data-testid="mark-override" /> : null}
            </g>
          </g>
        )
      })}
      <line x1={LEFT} x2={LEFT + PLOT_W} y1={AXIS_Y} y2={AXIS_Y} className="stroke-stage-muted" strokeWidth={1} />
      {blocks.slice(0, -1).map((block) => {
        if (disabled || !actionsFor(blocks, marks, block.index).drag) return null
        const shown = drag?.index === block.index ? drag.deltaSec : 0
        const x = xOf(totalMs, block.endMs + shown * 1000)
        const range = dragRange(blocks, block.index)
        const label = t('editor.tl.boundary', { n: block.index + 1, time: clock(block.endMs / 1000 + shown) })
        return (
          <g key={`edge-${String(block.index)}`}>
            {drag?.index === block.index ? <line x1={x} x2={x} y1={TOP - 6} y2={AXIS_Y} className="stroke-stage-text" strokeWidth={2} strokeDasharray="4 3" /> : null}
            <rect
              role="slider"
              tabIndex={0}
              data-handle={block.index}
              aria-label={label}
              aria-valuemin={block.endMs / 1000 - range.earlier}
              aria-valuemax={block.endMs / 1000 + range.later}
              aria-valuenow={block.endMs / 1000 + shown}
              aria-valuetext={clock(block.endMs / 1000 + shown)}
              x={x - 6}
              y={TOP}
              width={12}
              height={PLOT_H}
              className="cursor-ew-resize fill-transparent focus-visible:stroke-stage-text"
              strokeWidth={2}
              onKeyDown={(event) => {
                keys(block.index, event)
              }}
              onBlur={() => {
                if (drag?.index === block.index) finish(false)
              }}
              onPointerDown={(event) => {
                pointerDown(block.index, event)
              }}
              onPointerMove={(event) => {
                pointerMove(block.index, event)
              }}
              onPointerUp={() => {
                finish(true)
              }}
              onPointerCancel={() => {
                finish(false)
              }}
            />
          </g>
        )
      })}
      {clips.map((clip) => (
        <g key={clip.piece}>
          <rect
            data-piece={clip.piece}
            tabIndex={0}
            role="button"
            aria-label={t('editor.tl.song', { n: clip.piece + 1, title: clip.label })}
            x={xOf(totalMs, clip.startMs)}
            y={LANE_Y + (clip.piece % 2 === 0 ? 0 : 3)}
            width={Math.max(widthOf(totalMs, clip.endMs - clip.startMs), 1)}
            height={LANE_H - 3}
            rx={3}
            className={`${focusPiece === clip.piece ? 'fill-stage-muted' : 'fill-stage-grid'} stroke-stage-muted`}
            strokeWidth={1}
            fillOpacity={0.8}
            onMouseEnter={() => {
              onFocusPiece(clip.piece)
            }}
            onMouseLeave={() => {
              onFocusPiece(null)
            }}
            onFocus={() => {
              onFocusPiece(clip.piece)
            }}
            onBlur={() => {
              onFocusPiece(null)
            }}
            onClick={() => {
              onAudition(clip.startMs)
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onAudition(clip.startMs)
              }
            }}
          />
          <text x={xOf(totalMs, clip.startMs) + 4} y={LANE_Y + 16} fontSize={11} className="pointer-events-none fill-stage-text">
            {clip.piece + 1}
          </text>
        </g>
      ))}
      {cursorMs !== null ? (
        <line
          data-testid="cursor"
          data-cursor-ms={Math.round(cursorMs)}
          x1={xOf(totalMs, cursorMs)}
          x2={xOf(totalMs, cursorMs)}
          y1={TOP - 8}
          y2={LANE_Y + LANE_H}
          className="stroke-stage-text"
          strokeWidth={2}
        />
      ) : null}
    </svg>
  )
}
