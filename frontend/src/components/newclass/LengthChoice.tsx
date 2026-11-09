import { useEffect, useRef, useState } from 'react'
import { useT } from '../../i18n/I18n'
import { Choice } from './Choice'
import { TextInput } from './inputs'
import { LENGTHS, lengthSentence } from './formModel'
import type { SegmentedOption } from '../ui/segmented-control'

/**
 * The class length: the common lengths as one segmented control, and "Other" for a number of
 * minutes of one's own. The number field is there only for "Other", or when the length (a preset
 * link's, say) is not one of the common ones. `maxMinutes` is the plan's cap, null while unknown.
 */
export function LengthChoice({
  length,
  onChange,
  maxMinutes,
}: {
  length: string
  onChange: (length: string) => void
  maxMinutes: number | null
}) {
  const t = useT()
  const [other, setOther] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const justChose = useRef(false)
  const common = LENGTHS.some((m) => String(m) === length)
  const open = other || !common
  useEffect(() => {
    if (open && justChose.current) input.current?.focus()
    justChose.current = false
  }, [open])
  const options: SegmentedOption<string>[] = [
    ...LENGTHS.map((m) => ({
      value: String(m),
      label: t('newclass.minutes', { count: m }),
      disabled: maxMinutes !== null && m > maxMinutes,
    })),
    { value: 'other', label: t('newclass.otherLength') },
  ]
  // The cap is said once: under the number field, or, when a length is switched off, under the control.
  const capped = maxMinutes !== null && options.some((o) => o.disabled)
  return (
    <Choice
      label={t('newclass.classLength')}
      options={options}
      value={open ? 'other' : length}
      onValueChange={(value) => {
        if (value === 'other') {
          justChose.current = true
          setOther(true)
          input.current?.focus()
          return
        }
        setOther(false)
        onChange(value)
      }}
    >
      {open ? (
        <TextInput
          ref={input}
          label={t('newclass.lengthMinutes')}
          type="number"
          inputMode="numeric"
          min={10}
          max={maxMinutes === null ? 180 : Math.min(180, maxMinutes)}
          hint={maxMinutes === null ? undefined : lengthSentence(t, maxMinutes)}
          value={length}
          onChange={(e) => {
            onChange(e.target.value)
          }}
        />
      ) : capped ? (
        <p className="m-0 text-[0.8125rem] text-muted">{lengthSentence(t, maxMinutes)}</p>
      ) : null}
    </Choice>
  )
}
