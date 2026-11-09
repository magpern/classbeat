import { useT } from '../../i18n/I18n'
import { Stepper as UiStepper } from '../ui/stepper'

/** The shared stepper (`ui/stepper.tsx`) with the "min to max" hint of the New class form. */
export function Stepper(props: {
  label: string
  /** The button names, already worded: "Fewer climbs", "More climbs". */
  fewer: string
  more: string
  value: string
  min: number
  max: number
  onChange: (value: string) => void
}) {
  const t = useT()
  return <UiStepper {...props} hint={t('newclass.stepper.range', { min: props.min, max: props.max })} />
}
