import type { ClassSummary } from '../../api/classes'
import type { WorkspaceState } from '../../lib/composer/chip'
import { classStatus } from '../../lib/classCards'
import { useT } from '../../i18n/I18n'
import { Chip } from '../ui/chip'

/** The state chip of the class title line (ADR 0038 section 10.2). */
export function StateChip({ state, summary }: { state: WorkspaceState; summary: ClassSummary }) {
  const t = useT()
  switch (state) {
    case 'reading':
      return <Chip tone="warn">{t('cw.chip.reading')}</Chip>
    case 'rough':
      return <Chip tone="warn">{t('cw.chip.rough')}</Chip>
    case 'ready':
      return <Chip tone="ok">{t('cw.chip.ready')}</Chip>
    case 'expired':
      return <Chip tone="warn">{t('cw.chip.expired')}</Chip>
    case 'playlistNotCreated':
      return <Chip tone="bad">{t('cw.chip.playlistNotCreated')}</Chip>
    case 'creating':
      return <Chip tone="warn">{t('cw.chip.creating')}</Chip>
    case 'saving':
      return <Chip tone="warn">{t('cw.chip.saving')}</Chip>
    case 'saved':
      return <Chip tone="ok">{t('cw.chip.saved')}</Chip>
    case 'draft': {
      const status = classStatus(summary)
      return <Chip tone={status.tone}>{status.label}</Chip>
    }
  }
}
