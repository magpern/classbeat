import { useT } from '../../i18n/I18n'
import { cn } from '../../lib/cn'
import { useResolvedTheme } from '../../theme/useResolvedTheme'
import {
  FORMATS,
  PHONE_MEDIA,
  fileUrl,
  largest,
  resolveSlot,
  srcSet,
  type ImageSet,
  type SlotId,
} from './imageSlots'

interface Props {
  slot: SlotId
  /** The `sizes` attribute: how wide the picture is drawn, so the browser picks a file. */
  sizes: string
  /** The hero is the one picture loaded first; the others wait until they are near the screen. */
  priority?: boolean
  /** Sizes and places the box the picture fills (the picture covers it). */
  className?: string
}

/**
 * One slot as a `<picture>`: AVIF, then WebP, then JPEG; a phone crop when the slot has one;
 * width and height set so nothing shifts; the theme's picture, or none (`resolveSlot`).
 * Decorative slots get an empty alt and are hidden from assistive technology.
 */
export function SlotPicture({ slot, sizes, priority = false, className }: Props) {
  const t = useT()
  const theme = useResolvedTheme()
  const found = resolveSlot(slot, theme)
  if (!found) return null
  const { set, phone, dimmed, alt } = found
  const [width, height] = largest(set)
  const sources = (from: ImageSet, media?: string) =>
    FORMATS.map(({ ext, type }) => (
      <source key={`${media ?? 'wide'}-${ext}`} media={media} type={type} srcSet={srcSet(from, ext)} sizes={sizes} />
    ))
  return (
    <div className={cn('slot', dimmed && 'slot--dim', className)} data-slot={slot}>
      <picture>
        {phone ? sources(phone, PHONE_MEDIA) : null}
        {sources(set)}
        <img
          src={fileUrl(set, width, 'jpg')}
          width={width}
          height={height}
          alt={alt === null ? '' : t(alt)}
          aria-hidden={alt === null ? true : undefined}
          loading={priority ? 'eager' : 'lazy'}
          fetchPriority={priority ? 'high' : 'auto'}
          decoding="async"
        />
      </picture>
    </div>
  )
}
