import type { Revision } from '../../api/classes'
import { originLabel } from '../../api/classMessages'
import type { TFunction } from '../../i18n/t'

/** "Version 1, first design": the line under the class title for the version being shown. */
export function versionLine(t: TFunction, revision: Pick<Revision, 'number' | 'origin'>): string {
  return t('class.revision.heading', {
    number: revision.number,
    origin: originLabel(revision.origin).toLowerCase(),
  })
}
