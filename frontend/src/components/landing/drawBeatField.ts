import { BAR_ZONES, barLevel, beatPhase, kick, markerAngle, zoneOfLevel } from '../../lib/beatField'

export interface BeatFieldLook {
  /** Colours of zones 1 to 5, then the brand colour for the ring and the marker. */
  zones: readonly string[]
  brand: string
}

export interface BeatFieldFrame {
  width: number
  height: number
  /** Centre and outer radius of the flywheel, in CSS pixels. */
  cx: number
  cy: number
  radius: number
  bars: number
  seconds: number
  bpm: number
}

const TAU = Math.PI * 2

/** One frame: a soft glow that swells on the beat, a ring, bars climbing through the zones, a marker. */
export function drawBeatField(ctx: CanvasRenderingContext2D, look: BeatFieldLook, f: BeatFieldFrame): void {
  const { cx, cy, radius: R, bars, seconds, bpm } = f
  const hit = kick(beatPhase(seconds, bpm))
  ctx.clearRect(0, 0, f.width, f.height)
  ctx.lineCap = 'round'

  const glow = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 1.5)
  glow.addColorStop(0, tint(look.brand, 20 + hit * 16))
  glow.addColorStop(1, tint(look.brand, 0))
  ctx.fillStyle = glow
  ctx.fillRect(0, 0, f.width, f.height)

  const inner = R * (0.6 + hit * 0.025)
  ctx.globalAlpha = 0.45
  ctx.strokeStyle = look.brand
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.arc(cx, cy, inner, 0, TAU)
  ctx.stroke()

  // Bars, one stroke per zone colour (five paths, not one per bar). Whole ring turns slowly.
  const levels = Array.from({ length: bars }, (_, i) => barLevel(i, bars, seconds, bpm))
  const spin = seconds * 0.04 - Math.PI / 2
  ctx.globalAlpha = 0.85
  ctx.lineWidth = Math.max(2, ((TAU * inner) / bars) * 0.5)
  for (let zone = 1; zone <= BAR_ZONES; zone++) {
    ctx.strokeStyle = look.zones[zone - 1] ?? look.brand
    ctx.beginPath()
    levels.forEach((level, i) => {
      if (zoneOfLevel(level) !== zone) return
      const angle = (i / bars) * TAU + spin
      const from = inner + R * 0.06
      const to = from + R * 0.34 * level
      ctx.moveTo(cx + Math.cos(angle) * from, cy + Math.sin(angle) * from)
      ctx.lineTo(cx + Math.cos(angle) * to, cy + Math.sin(angle) * to)
    })
    ctx.stroke()
  }

  // The marker, a bright dot on the ring with a short fading tail.
  const head = markerAngle(seconds, bpm)
  ctx.strokeStyle = look.brand
  ctx.lineWidth = 3
  for (let s = 0; s < 14; s++) {
    ctx.globalAlpha = 0.9 * (1 - s / 14)
    ctx.beginPath()
    ctx.arc(cx, cy, inner, head - (s + 1) * 0.045, head - s * 0.045)
    ctx.stroke()
  }
  ctx.globalAlpha = 1
  ctx.fillStyle = look.brand
  ctx.beginPath()
  ctx.arc(cx + Math.cos(head) * inner, cy + Math.sin(head) * inner, 4 + hit * 2, 0, TAU)
  ctx.fill()
}

/** A token's colour at `percent` strength (0 to 100) over nothing: canvas reads `color-mix` itself. */
function tint(colour: string, percent: number): string {
  return `color-mix(in srgb, ${colour} ${String(percent)}%, transparent)`
}
