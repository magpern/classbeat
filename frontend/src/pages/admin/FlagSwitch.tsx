import { asText, type Flag } from '../../api/adminSettings'
import { Switch } from '../../components/ui/switch'
import { REASONS, Reason } from './settingsReasons'

// The on/off form of `composer_flow` and `running_enabled` (#1289). The switch shows the stored
// value, or the effective one when nothing is stored, and saves on each change (`on` / `off`).
// The backend stores `on` even while the server's own switch is off, where it has no effect, so
// the switch stays usable and a note says what the server needs first. A refused write (the
// `running_enabled` coverage check) shows its reason through the card's error notice.

const LABELS: Record<string, { label: string; envOff: string }> = {
  composer_flow: { label: 'Composer flow', envOff: REASONS.composerFlowOff },
  running_enabled: { label: 'Running classes', envOff: REASONS.runningOff },
}

export function FlagSwitch({ item, busy, onSave }: { item: Flag; busy: boolean; onSave: (v: 'on' | 'off') => void }) {
  const stored = asText(item.stored)
  const on = (item.stored !== null && item.storedValid ? stored : asText(item.effective)) === 'on'
  const text = LABELS[item.key]
  const envOff = item.environment === 'off'
  // A stored `on` that the server's own switch cancels is on in the form but not in effect: the
  // backend then reports the effective value `off`, so that alone says it.
  const notInEffect = item.stored !== null && item.storedValid && stored === 'on' && asText(item.effective) !== 'on'
  const noteId = `note-${item.key}`
  const id = `switch-${item.key}`
  const reasonId = `reason-${item.key}`
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Switch
        id={id}
        checked={on}
        disabled={busy}
        aria-describedby={envOff ? reasonId : undefined}
        onCheckedChange={(next) => {
          onSave(next ? 'on' : 'off')
        }}
      />
      <label htmlFor={id}>{text?.label ?? item.key}</label>
      {notInEffect ? (
        <span id={noteId} data-testid={noteId} className="text-[0.9375rem] font-medium text-muted">
          {REASONS.notInEffect}
        </span>
      ) : null}
      {envOff ? <Reason id={reasonId}>{text?.envOff}</Reason> : null}
    </div>
  )
}
