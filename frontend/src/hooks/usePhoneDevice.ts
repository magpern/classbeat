/**
 * True on a handheld phone whatever the window width (landscape, "desktop site" mode): a touch
 * screen whose short side is under 640 px. A phone cannot scan its own screen (ADR 0024), so the
 * MOWL QR code must not be offered there. The viewport width alone (`useLayout`) is not enough.
 */
export function isPhoneDevice(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  if (!window.matchMedia('(pointer: coarse)').matches) return false
  const short = Math.min(window.screen.width, window.screen.height)
  return short > 0 && short < 640
}
