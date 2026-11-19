import { useT } from '../../i18n/I18n'

/** "Uses 1 of your N classes left this month" beside the Design button; nothing while the plan is unknown. */
export function ClassesLeft({ count }: { count: number | null }) {
  const t = useT()
  if (count === null) return null
  return (
    <span className="min-w-0 text-[0.875rem] text-muted">
      {count > 0 ? t('newclass.side.classesLeft', { count }) : t('newclass.side.noClassesLeft')}
    </span>
  )
}
