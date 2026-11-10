/**
 * Zone colours as static Tailwind classes. The CSP refuses `style="…"` in served HTML, so the
 * public pages' prerender cannot use `style={}` (ADR 0030). The class names are written out in
 * full so Tailwind's scanner finds them; index 0 is zone 1.
 */
const FILL = [
  'fill-zone-1',
  'fill-zone-2',
  'fill-zone-3',
  'fill-zone-4',
  'fill-zone-5',
  'fill-zone-6',
  'fill-zone-7',
] as const

const STOP_TOP = [
  '[stop-color:var(--cb-zone-1)]',
  '[stop-color:var(--cb-zone-2)]',
  '[stop-color:var(--cb-zone-3)]',
  '[stop-color:var(--cb-zone-4)]',
  '[stop-color:var(--cb-zone-5)]',
  '[stop-color:var(--cb-zone-6)]',
  '[stop-color:var(--cb-zone-7)]',
] as const

const STOP_BOTTOM = [
  '[stop-color:color-mix(in_srgb,var(--cb-zone-1)_60%,var(--cb-paper))]',
  '[stop-color:color-mix(in_srgb,var(--cb-zone-2)_60%,var(--cb-paper))]',
  '[stop-color:color-mix(in_srgb,var(--cb-zone-3)_60%,var(--cb-paper))]',
  '[stop-color:color-mix(in_srgb,var(--cb-zone-4)_60%,var(--cb-paper))]',
  '[stop-color:color-mix(in_srgb,var(--cb-zone-5)_60%,var(--cb-paper))]',
  '[stop-color:color-mix(in_srgb,var(--cb-zone-6)_60%,var(--cb-paper))]',
  '[stop-color:color-mix(in_srgb,var(--cb-zone-7)_60%,var(--cb-paper))]',
] as const

function pick(list: readonly string[], zone: number): string {
  return list[Math.min(Math.max(Math.round(zone), 1), 7) - 1] ?? ''
}

export const zoneFill = (zone: number) => pick(FILL, zone)
export const zoneStopTop = (zone: number) => pick(STOP_TOP, zone)
export const zoneStopBottom = (zone: number) => pick(STOP_BOTTOM, zone)
