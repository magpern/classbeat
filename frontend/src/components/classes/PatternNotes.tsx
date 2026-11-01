import { Info, StickyNote } from 'lucide-react'
import { useRef, useState } from 'react'
import { dismissRule, showRuleAgain, type RuleHit } from '../../api/classes'
import {
  EXPLAINED_REASON,
  MAX_NOTES,
  NOTES_USE_CURRENT_RULES,
  RULE_COPY,
  valueText,
} from '../../lib/patternNotes'

const ACTION =
  'inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center rounded-full border-0 bg-transparent px-3 text-[0.9375rem] text-text underline underline-offset-2 hover:bg-raised focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand'

function without(set: ReadonlySet<string>, rule: string): Set<string> {
  const next = new Set(set)
  next.delete(rule)
  return next
}

/**
 * "Pattern notes" (ADR 0017 section 4): a quiet, collapsed block of what is usual in classes of
 * this kind. Notes only: nothing here disables an action and nothing is announced as an alert; the
 * one `role="status"` line carries a failed dismiss or undo. A dismissed note stays in its place as
 * "Show again" (undo is offered once, on this page), and focus follows the change.
 */
export function PatternNotes({
  classId,
  hits,
  notesUseCurrentRules,
}: {
  classId: string
  hits: RuleHit[]
  notesUseCurrentRules: boolean
}) {
  // Rule ids dismissed on this page (kept across versions of the class), and those being sent.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set())
  const [failure, setFailure] = useState('')
  const focusRule = useRef<string | null>(null)
  const notes = hits.filter((hit) => Object.hasOwn(RULE_COPY, hit.rule)).slice(0, MAX_NOTES)

  if (notes.length === 0) return null

  const flip = (rule: string, on: boolean) => {
    setHidden((before) => {
      const next = new Set(before)
      if (on) next.add(rule)
      else next.delete(rule)
      return next
    })
    focusRule.current = rule
  }

  const change = (rule: string, dismiss: boolean) => {
    // A disabled button would lose the focus, so a second click while one is sent is ignored.
    if (pending.has(rule)) return
    setFailure('')
    flip(rule, dismiss)
    setPending((before) => new Set(before).add(rule))
    const call = dismiss ? dismissRule(classId, rule) : showRuleAgain(classId, rule)
    call
      .catch(() => {
        flip(rule, !dismiss)
        setFailure(
          dismiss
            ? 'The note could not be dismissed. Try again in a moment.'
            : 'The note could not be shown again. Try again in a moment.',
        )
      })
      .finally(() => {
        setPending((before) => without(before, rule))
      })
  }

  const remember = (rule: string) => (node: HTMLButtonElement | null) => {
    if (node === null) return
    // The control that replaces the one just used takes the focus when it is attached.
    if (focusRule.current === rule) {
      focusRule.current = null
      node.focus()
    }
  }

  return (
    <details className="rounded-md border border-line bg-surface" data-testid="pattern-notes">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-[0.9375rem] text-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand">
        <StickyNote aria-hidden="true" size={16} />
        <span>
          Pattern notes <span className="tabular-nums">({notes.length})</span>
        </span>
      </summary>
      <div className="flex flex-col gap-2 px-4 pb-3">
        <p className="m-0 text-[0.8125rem] text-muted">
          What is usual in classes of this kind. Your brief comes first; these never block anything.
        </p>
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {notes.map((hit) => {
            const quiet = hit.explainedByBrief || hit.severity === 'info'
            const copy = RULE_COPY[hit.rule]
            if (hidden.has(hit.rule)) {
              return (
                <li
                  key={hit.rule}
                  className="flex flex-wrap items-center gap-x-3 text-[0.9375rem] text-muted"
                  data-testid="pattern-note-hidden"
                >
                  <span>Note hidden.</span>
                  <button
                    ref={remember(hit.rule)}
                    type="button"
                    className={ACTION}
                    aria-label={`Show again: ${copy}`}
                    onClick={() => {
                      change(hit.rule, false)
                    }}
                  >
                    Show again
                  </button>
                </li>
              )
            }
            const label = hit.explainedByBrief
              ? 'Matches your brief'
              : hit.severity === 'warn'
                ? 'Worth a look'
                : 'Good to know'
            return (
              <li
                key={hit.rule}
                className={`flex flex-wrap items-center gap-x-3 gap-y-1 py-1 text-[0.9375rem] ${quiet ? 'text-muted' : 'text-text'}`}
                data-testid="pattern-note"
                data-severity={hit.severity}
                data-explained={hit.explainedByBrief ? 'true' : 'false'}
              >
                <span className="flex min-w-0 flex-1 basis-64 items-start gap-2">
                  <Info aria-hidden="true" size={16} className="mt-1 shrink-0" />
                  <span className="flex flex-col">
                    <span>
                      <strong className="font-medium">{label}.</strong> {copy}
                    </span>
                    <span className="text-[0.8125rem] text-muted">{valueText(hit)}</span>
                    {hit.explainedByBrief ? (
                      <span className="text-[0.8125rem] text-muted">{EXPLAINED_REASON}</span>
                    ) : null}
                  </span>
                </span>
                <button
                  ref={remember(hit.rule)}
                  type="button"
                  className={ACTION}
                  aria-label={`Dismiss: ${copy}`}
                  onClick={() => {
                    change(hit.rule, true)
                  }}
                >
                  Dismiss
                </button>
              </li>
            )
          })}
        </ul>
        {notesUseCurrentRules ? (
          <p className="m-0 text-[0.8125rem] text-muted" data-testid="notes-current-rules">
            {NOTES_USE_CURRENT_RULES}
          </p>
        ) : null}
        <p
          role="status"
          className="m-0 text-[0.8125rem] text-text empty:hidden"
          data-testid="pattern-notes-status"
        >
          {failure}
        </p>
      </div>
    </details>
  )
}
