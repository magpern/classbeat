import { useEffect, useState } from 'react'
import { getBanner, type Banner as BannerData } from '../api/plan'

/** The owner's notice from `GET /api/settings/public` on every page. Always text, never HTML. */
export function Banner() {
  const [banner, setBanner] = useState<BannerData | null>(null)
  useEffect(() => {
    const abort = new AbortController()
    getBanner(abort.signal).then(setBanner, () => {
      // No banner is better than a broken page.
    })
    return () => {
      abort.abort()
    }
  }, [])
  if (banner === null) return null
  return (
    <p className="banner" data-level={banner.level} role="status" data-testid="banner">
      {banner.text}
    </p>
  )
}
