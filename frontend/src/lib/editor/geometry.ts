// The editor timeline's geometry: where class time falls on the drawing, and how far a pointer
// drag of so many pixels moved a boundary. Pure.

export const VIEW_W = 720
export const LEFT = 8
export const PLOT_W = VIEW_W - LEFT * 2


/** The x of class time `ms` in drawing units. */
export function xOf(totalMs: number, ms: number): number {
  return totalMs <= 0 ? LEFT : LEFT + (Math.max(0, Math.min(totalMs, ms)) / totalMs) * PLOT_W
}

/** The width in drawing units of `ms` of class time. */
export function widthOf(totalMs: number, ms: number): number {
  return totalMs <= 0 ? 0 : (ms / totalMs) * PLOT_W
}

/**
 * A pointer moved `dxPx` screen pixels on a drawing that is `svgWidthPx` wide: that many whole
 * seconds of class time. A drawing with no width (not laid out yet) moves nothing.
 */
export function pxToDeltaSec(dxPx: number, svgWidthPx: number, totalSec: number): number {
  if (svgWidthPx <= 0 || totalSec <= 0) return 0
  return Math.round(((dxPx * (VIEW_W / svgWidthPx)) / PLOT_W) * totalSec)
}
