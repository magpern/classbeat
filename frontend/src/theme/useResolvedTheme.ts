import { useEffect, useState } from 'react'
import { lightPreferred, resolvedTheme } from './theme'

/**
 * The page's resolved theme (`dark` or `light`), kept current when the switch changes the
 * `data-theme` attribute or, under `system`, when the device's preference changes.
 */
export function useResolvedTheme(): 'dark' | 'light' {
  const [theme, setTheme] = useState(resolvedTheme)
  useEffect(() => {
    const update = () => {
      setTheme(resolvedTheme())
    }
    const observer = new MutationObserver(update)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const media = lightPreferred()
    media?.addEventListener('change', update)
    return () => {
      observer.disconnect()
      media?.removeEventListener('change', update)
    }
  }, [])
  return theme
}
