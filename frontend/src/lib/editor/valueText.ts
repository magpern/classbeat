import type { DraftGame } from '../../api/draftTypes'
import { t } from '../../i18n/t'
import { gameLabel } from '../games'
import { zoneName } from '../profile'
import { clock } from '../workout'

// A block field's value as words, for the "Your edit kept (AI suggested ...)" badge.

/** The words for the value of `field` (an attribute, or `boundary`: the block's end in ms of class time). */
export function valueText(field: string, value: unknown): string {
  if (field === 'zone' && typeof value === 'number') return t('class.zone.line', { zone: value, name: zoneName(value) })
  if (field === 'rpm') {
    if (value === 'beat') return t('editor.value.beat')
    if (Array.isArray(value) && value.length === 2) return t('editor.value.rpm', { from: String(value[0]), to: String(value[1]) })
  }
  if (field === 'standing' && typeof value === 'boolean') return value ? t('editor.value.standing') : t('editor.value.seated')
  if (field === 'cue') return typeof value === 'string' && value !== '' ? t('editor.value.cue', { cue: value }) : t('editor.value.noCue')
  if (field === 'game') return typeof value === 'string' ? gameLabel(value as DraftGame) : t('editor.value.noGame')
  if (field === 'boundary' && typeof value === 'number') return t('editor.value.boundary', { time: clock(value / 1000) })
  return t('editor.value.unknown')
}

/** The field's name in words. */
export function fieldName(field: string): string {
  const names: Record<string, string> = {
    zone: t('editor.field.zone'),
    rpm: t('editor.field.rpm'),
    standing: t('editor.field.standing'),
    cue: t('editor.field.cue'),
    game: t('editor.field.game'),
    boundary: t('editor.field.boundary'),
  }
  const found = Object.hasOwn(names, field) ? names[field] : undefined
  return found ?? t('editor.field.unknown')
}
